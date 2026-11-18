/* Camera controls after a single-point structure opens in the viewer.
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
    const eye = gd && gd.layout && gd.layout.scene && gd.layout.scene.camera && gd.layout.scene.camera.eye;
    return !!(scene && scene.glplot && gd.data && gd.data.length && eye);
  }, null, { timeout: 90000 });
}

async function readView(page) {
  return page.evaluate(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const camera = gd && gd.layout && gd.layout.scene && gd.layout.scene.camera;
    const eye = camera && camera.eye;
    const banner = document.querySelector("#status-banner");
    const dashError = document.querySelector(".dash-error, .error-container");
    return {
      eye: eye ? { x: Number(eye.x), y: Number(eye.y), z: Number(eye.z) } : null,
      banner: banner ? String(banner.textContent || "") : "",
      dashError: dashError ? String(dashError.textContent || "") : "",
    };
  });
}

function assertHealthy(view, label) {
  if (view.dashError) fail(label + " dash error: " + view.dashError);
  if (/IndexError|camera error|could not be restored|relayout failed/i.test(view.banner)) {
    fail(label + " status: " + view.banner);
  }
  if (!view.eye) fail(label + " missing eye");
  const values = [view.eye.x, view.eye.y, view.eye.z];
  if (values.some((value) => !Number.isFinite(value))) fail(label + " non-finite eye");
}

async function drag(page) {
  const box = await page.locator("#crystal-graph canvas").first().boundingBox();
  if (!box) fail("graph has no box");
  const x = box.x + box.width * 0.55;
  const y = box.y + box.height * 0.45;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 80, y + 36, { steps: 8 });
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
    assertHealthy(await readView(page), "initial");
    await drag(page);
    await page.waitForTimeout(400);
    assertHealthy(await readView(page), "after drag");
    await drag(page);
    await page.waitForTimeout(400);
    assertHealthy(await readView(page), "second drag");
    await page.locator("#view-reset").click();
    await page.waitForTimeout(700);
    assertHealthy(await readView(page), "after reset");
    await page.locator("#view-reset").click();
    await page.waitForTimeout(400);
    assertHealthy(await readView(page), "second reset");
    const tabs = page.locator("#scene-tabs .tab");
    if (await tabs.count() < 2) fail("expected two scenes");
    await tabs.nth(1).click();
    await graphReady(page);
    assertHealthy(await readView(page), "second scene");
    await drag(page);
    await page.waitForTimeout(400);
    assertHealthy(await readView(page), "second scene drag");
    await tabs.nth(0).click();
    await graphReady(page);
    assertHealthy(await readView(page), "returned scene");
    await page.locator("#view-projection input[value='orthographic']").click();
    await page.waitForTimeout(400);
    assertHealthy(await readView(page), "orthographic");
    await page.locator("#view-projection input[value='perspective']").click();
    await page.waitForTimeout(400);
    assertHealthy(await readView(page), "perspective");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
