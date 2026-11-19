/* Flat ORTEP camera interaction on the real viewer.
 * Args: playwright module, chrome path, base url.
 */
const { spawnSync } = require("node:child_process");
const { chromium } = require(process.argv[2]);
const chrome = process.argv[3];
const base = process.argv[4];
const python = process.env.MV_PYTHON;

function fail(message) {
  throw new Error(message);
}

async function graphReady(page) {
  await page.waitForFunction(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    return !!(scene && scene.glplot && gd.data && gd.data.length);
  }, null, { timeout: 90000 });
}

async function readCamera(page) {
  return page.evaluate(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const camera = gd && gd.layout && gd.layout.scene && gd.layout.scene.camera;
    const eye = camera && camera.eye;
    const banner = document.querySelector("#status-banner");
    const dashError = document.querySelector(".dash-error, .error-container");
    const images = gd && gd.layout && gd.layout.images;
    return {
      eye: eye ? { x: Number(eye.x), y: Number(eye.y), z: Number(eye.z) } : null,
      keys: camera ? Object.keys(camera) : [],
      images: images ? images.length : 0,
      banner: banner ? String(banner.textContent || "") : "",
      renderStatus: (() => {
        const node = document.getElementById("mv-render-status");
        return node && !node.hidden ? String(node.textContent || "") : "";
      })(),
      dashError: dashError ? String(dashError.textContent || "") : "",
      title: String((gd && gd.layout && gd.layout.title && (gd.layout.title.text || gd.layout.title)) || ""),
    };
  });
}

function assertHealthy(view, label) {
  if (view.dashError) fail(label + " dash error: " + view.dashError);
  const statusText = String(view.renderStatus || "") + " " + String(view.banner || "");
  if (/invalid property|camera error|could not be restored|did not match the view|relayout failed/i.test(statusText)) {
    fail(label + " status: " + statusText);
  }
  if (!view.eye || !view.keys.includes("eye")) fail(label + " missing eye " + JSON.stringify(view));
  if (view.keys.includes("position") || view.keys.includes("focal_point")) {
    fail(label + " legacy camera keys " + JSON.stringify(view.keys));
  }
  const values = [view.eye.x, view.eye.y, view.eye.z];
  if (values.some((value) => !Number.isFinite(value))) fail(label + " non-finite eye");
}

async function selectDropdown(page, id, label) {
  await page.evaluate((elementId) => {
    const node = document.getElementById(elementId);
    if (!node) throw new Error("missing control " + elementId);
    node.scrollIntoView({ block: "center", inline: "nearest" });
  }, id);
  const host = page.locator("#" + id);
  const trigger = host.locator(".dash-dropdown-trigger, .Select-control").first();
  await trigger.click();
  const option = page.locator(".dash-dropdown-option, .VirtualizedSelectOption, [role='option']").filter({ hasText: label }).first();
  await option.click();
}

function sceneBackgroundIsClear(value) {
  const text = String(value || "").replace(/\s+/g, "").toLowerCase();
  return text === "transparent" || text === "rgba(0,0,0,0)";
}

async function publicationRaster(page) {
  return page.evaluate(() => new Promise((resolve) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const scene = gd && gd.layout && gd.layout.scene;
    const image = gd && gd.layout && gd.layout.images && gd.layout.images[0];
    const source = image && image.source;
    if (!source) {
      resolve({ error: "no image", bg: scene && scene.bgcolor });
      return;
    }
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = img.width;
      canvas.height = img.height;
      const context = canvas.getContext("2d");
      context.drawImage(img, 0, 0);
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let ink = 0;
      const step = 16;
      for (let i = 0; i < data.length; i += step) {
        if (data[i] < 245 || data[i + 1] < 245 || data[i + 2] < 245) ink += 1;
      }
      resolve({
        bg: scene && scene.bgcolor,
        ink,
        width: img.width,
        height: img.height,
      });
    };
    img.onerror = () => resolve({ error: "image decode", bg: scene && scene.bgcolor });
    img.src = source;
  }));
}

function publicationReady(raster) {
  return !!(raster && !raster.error && sceneBackgroundIsClear(raster.bg) && raster.ink);
}

function compositedInk(png) {
  if (!python) fail("MV_PYTHON is required to read the composited graph");
  const result = spawnSync(python, ["-c", `
import io, sys
from PIL import Image
image = Image.open(io.BytesIO(sys.stdin.buffer.read())).convert("RGB")
width, height = image.size
# The caption sits on the bottom strip. The plot above it is the picture
# the user sees, including a blank canvas that still has a paper image.
crop = image.crop((0, 0, width, max(1, int(height * 0.9))))
pixels = list(crop.getdata())
ink = sum(1 for red, green, blue in pixels if red < 245 or green < 245 or blue < 245)
print(ink, len(pixels))
`], { input: png });
  if (result.status !== 0) fail("composited decode failed: " + String(result.stderr || ""));
  const parts = String(result.stdout || "").trim().split(/\s+/);
  const ink = Number(parts[0]);
  const total = Number(parts[1]);
  if (!Number.isFinite(ink) || !Number.isFinite(total) || total <= 0) fail("composited decode was empty");
  return { ink, total, fraction: ink / total };
}

async function assertPublicationVisible(page, label) {
  const deadline = Date.now() + 90000;
  let previous = null;
  let ready = false;
  while (Date.now() < deadline) {
    const raster = await publicationRaster(page);
    const shot = await page.locator("#crystal-graph").screenshot({ type: "png" });
    const composited = compositedInk(shot);
    const shown = publicationReady(raster) && composited.fraction > 0.008;
    if (ready && !shown) {
      fail(label + " publication picture disappeared " + JSON.stringify({ previous, raster, composited }));
    }
    if (shown) {
      ready = true;
      const againShot = await page.locator("#crystal-graph").screenshot({ type: "png" });
      const again = compositedInk(againShot);
      if (ready && !(again.fraction > 0.008)) {
        fail(label + " publication picture disappeared " + JSON.stringify({ composited, again }));
      }
      return;
    }
    previous = { raster, composited };
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
  }
  fail(label + " publication picture did not stay visible " + JSON.stringify(previous));
}

async function assertProjectionApplied(page, projection, startAttempts) {
  const deadline = Date.now() + 20000;
  let stable = 0;
  let last = null;
  while (Date.now() < deadline) {
    last = await page.evaluate(() => {
      const graph = document.querySelector("#crystal-graph .js-plotly-plot");
      const scene = graph && graph._fullLayout && graph._fullLayout.scene && graph._fullLayout.scene._scene;
      const status = document.getElementById("mv-render-status");
      const radio = document.querySelector("#view-projection input:checked");
      const diag = window.__mv_camera_diag || {};
      return {
        text: status && !status.hidden ? String(status.textContent || "") : "",
        radio: radio && radio.value,
        ortho: scene && scene.camera ? scene.camera._ortho : null,
        attempts: Number(diag.attempts || 0),
      };
    });
    if (/could not be restored|did not match the view|relayout failed/i.test(last.text)) {
      fail("projection " + projection + " " + last.text);
    }
    const started = last.attempts > startAttempts;
    const matches = last.radio === projection && (projection === "orthographic" ? last.ortho === true : last.ortho === false);
    if (started && matches && !last.text) {
      stable += 1;
      if (stable >= 2) {
        assertHealthy(await readCamera(page), "projection " + projection);
        await assertPublicationVisible(page, "projection " + projection);
        return;
      }
    } else {
      stable = 0;
    }
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
  }
  fail("projection " + projection + " did not settle " + JSON.stringify(last));
}

async function applyProjection(page, projection) {
  const startAttempts = await page.evaluate(() => Number((window.__mv_camera_diag || {}).attempts || 0));
  await page.locator("#view-projection input[value='" + projection + "']").click();
  await assertProjectionApplied(page, projection, startAttempts);
}

async function drag(page) {
  const box = await page.locator("#crystal-graph .js-plotly-plot").boundingBox();
  if (!box) fail("graph has no box");
  const x = box.x + box.width * 0.55;
  const y = box.y + box.height * 0.45;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 90, y + 40, { steps: 10 });
  await page.mouse.up();
}

async function main() {
  const browser = await chromium.launch({
    headless: process.env.MV_HEADED !== "1",
    executablePath: chrome,
    args: ["--use-gl=angle", "--use-angle=swiftshader"],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  try {
    await page.goto(base, { waitUntil: "domcontentloaded" });
    await graphReady(page);
    const before = await readCamera(page);
    assertHealthy(before, "initial mesh");

    await selectDropdown(page, "material-selector", "Flat shading (fast 3D)");
    await selectDropdown(page, "style-selector", "ORTEP");
    await page.waitForFunction(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const images = gd && gd.layout && gd.layout.images;
      const camera = gd && gd.layout && gd.layout.scene && gd.layout.scene.camera;
      return !!(images && images.length && camera && camera.eye && !camera.position);
    }, null, { timeout: 90000 });
    const flat = await readCamera(page);
    assertHealthy(flat, "flat ortep");
    await assertPublicationVisible(page, "flat ortep");

    await drag(page);
    await assertPublicationVisible(page, "after drag");
    const dragged = await readCamera(page);
    assertHealthy(dragged, "after drag");
    await drag(page);
    await assertPublicationVisible(page, "second drag");

    await page.locator("#view-reset").click();
    await assertPublicationVisible(page, "after reset");
    assertHealthy(await readCamera(page), "after reset");
    await applyProjection(page, "orthographic");
    await applyProjection(page, "perspective");
    await page.locator("#view-reset").click();
    await assertPublicationVisible(page, "second reset");
    assertHealthy(await readCamera(page), "second reset");

    const tabs = page.locator("#scene-tabs .tab");
    const tabCount = await tabs.count();
    if (tabCount < 2) fail("expected two scenes, saw " + tabCount);
    await tabs.nth(1).click();
    await graphReady(page);
    await selectDropdown(page, "material-selector", "Flat shading (fast 3D)");
    await selectDropdown(page, "style-selector", "ORTEP");
    await assertPublicationVisible(page, "second scene flat ortep");
    await page.locator("#view-reset").click();
    await assertPublicationVisible(page, "second scene reset");
    await tabs.nth(0).click();
    await graphReady(page);
    await assertPublicationVisible(page, "returned scene");

    await selectDropdown(page, "material-selector", "3D Mesh");
    await selectDropdown(page, "style-selector", "Ball-stick");
    await page.waitForFunction(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const camera = gd && gd.layout && gd.layout.scene && gd.layout.scene.camera;
      const images = gd && gd.layout && gd.layout.images;
      return !!(camera && camera.eye && !(images && images.length));
    }, null, { timeout: 90000 });
    assertHealthy(await readCamera(page), "mesh ball-stick");

    await page.locator("#view-projection input[value='orthographic']").click();
    await page.waitForFunction(() => {
      const radio = document.querySelector("#view-projection input:checked");
      return radio && radio.value === "orthographic";
    }, null, { timeout: 20000 });
    assertHealthy(await readCamera(page), "orthographic mesh");
    await page.locator("#view-projection input[value='perspective']").click();
    await page.waitForFunction(() => {
      const radio = document.querySelector("#view-projection input:checked");
      return radio && radio.value === "perspective";
    }, null, { timeout: 20000 });
    assertHealthy(await readCamera(page), "perspective mesh");
  } catch (error) {
    await page.screenshot({ path: "/tmp/flat-ortep-fail.png", fullPage: true }).catch(() => {});
    const info = await page.evaluate(() => {
      const node = document.getElementById("material-selector");
      return {
        html: node ? node.outerHTML.slice(0, 1800) : null,
        rect: node ? node.getBoundingClientRect().toJSON() : null,
      };
    }).catch(() => null);
    console.error("CONTROL " + JSON.stringify(info));
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch(async (error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
