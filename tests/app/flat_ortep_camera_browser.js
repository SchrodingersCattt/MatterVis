/* Flat ORTEP camera interaction on the real viewer.
 * Args: playwright module, chrome path, base url.
 */
const { chromium } = require(process.argv[2]);
const chrome = process.argv[3];
const base = process.argv[4];

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
      dashError: dashError ? String(dashError.textContent || "") : "",
      title: String((gd && gd.layout && gd.layout.title && (gd.layout.title.text || gd.layout.title)) || ""),
    };
  });
}

function assertHealthy(view, label) {
  if (view.dashError) fail(label + " dash error: " + view.dashError);
  if (/invalid property|camera error|could not be restored|relayout failed/i.test(view.banner)) {
    fail(label + " status: " + view.banner);
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
    if (!flat.images) fail("flat ortep image missing");

    await drag(page);
    await page.waitForTimeout(500);
    const dragged = await readCamera(page);
    assertHealthy(dragged, "after drag");
    await drag(page);
    await page.waitForTimeout(500);
    assertHealthy(await readCamera(page), "second drag");

    await page.locator("#view-reset").click();
    await page.waitForTimeout(800);
    assertHealthy(await readCamera(page), "after reset");
    await page.locator("#view-reset").click();
    await page.waitForTimeout(400);
    assertHealthy(await readCamera(page), "second reset");

    const tabs = page.locator("#scene-tabs .tab");
    const tabCount = await tabs.count();
    if (tabCount < 2) fail("expected two scenes, saw " + tabCount);
    await tabs.nth(1).click();
    await graphReady(page);
    await page.waitForTimeout(400);
    const other = await readCamera(page);
    assertHealthy(other, "second scene");
    await tabs.nth(0).click();
    await graphReady(page);
    assertHealthy(await readCamera(page), "returned scene");

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
    await page.waitForTimeout(400);
    assertHealthy(await readCamera(page), "orthographic");
    await page.locator("#view-projection input[value='perspective']").click();
    await page.waitForTimeout(400);
    assertHealthy(await readCamera(page), "perspective");
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
