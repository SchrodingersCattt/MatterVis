/* Drive Commit supercell on the open page.
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

function selectedScene() {
  const marked = document.querySelector("#scene-tabs .tab--selected, #scene-tabs [aria-selected='true']");
  const owned = marked && (marked.closest("[id^='scene-tab-']") || marked);
  return owned ? String(owned.id || "").replace(/^scene-tab-/, "") : "";
}

async function sites(page, sceneId) {
  return page.evaluate(async (id) => {
    const response = await fetch("/api/v2/export/cif?json=1&scene_id=" + encodeURIComponent(id));
    const body = await response.json();
    return String(body.content || "").split("\n").filter((line) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("_") || trimmed.startsWith("loop_") || trimmed.startsWith("#") || trimmed.startsWith("data_")) return false;
      return /(^|\s)(Na|Cl)(\s|$)/.test(trimmed);
    }).length;
  }, sceneId);
}

async function setCounts(page, counts) {
  await page.evaluate((values) => {
    window.dash_clientside.set_props("operate-supercell-a", {value: values.a});
    window.dash_clientside.set_props("operate-supercell-b", {value: values.b});
    window.dash_clientside.set_props("operate-supercell-c", {value: values.c});
  }, counts);
}

async function commit(page) {
  const before = await page.locator("#operate-status").textContent();
  await page.locator("#operate-supercell-btn").click();
  await page.waitForFunction((previous) => {
    const text = document.getElementById("operate-status")?.textContent || "";
    return text && text !== previous && /revision/.test(text);
  }, before || "", { timeout: 30000 });
  return page.locator("#operate-status").textContent();
}

async function main() {
  const browser = await chromium.launch({
    executablePath: chrome,
    headless: true,
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--ignore-gpu-blocklist"],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const other = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  try {
    await page.goto(base, { waitUntil: "domcontentloaded" });
    await graphReady(page);
    await page.waitForFunction(selectedScene, null, { timeout: 20000 });
    await other.goto(base, { waitUntil: "domcontentloaded" });
    await graphReady(other);
    await other.waitForFunction(selectedScene, null, { timeout: 20000 });
    const otherBefore = await other.evaluate(selectedScene);
    const source = await page.evaluate(selectedScene);
    if (!source) fail("the source scene tab is missing");
    await page.locator("#scene-new-tab-btn").click();
    await page.waitForFunction((previous) => {
      const tabs = document.querySelectorAll("#scene-tabs [id^='scene-tab-']");
      const marked = document.querySelector("#scene-tabs .tab--selected, #scene-tabs [aria-selected='true']");
      const owned = marked && (marked.closest("[id^='scene-tab-']") || marked);
      const id = owned ? String(owned.id || "").replace(/^scene-tab-/, "") : "";
      return tabs.length >= 2 && id && id !== previous;
    }, source, { timeout: 20000 });
    const copy = await page.evaluate(selectedScene);
    const copiedSites = await sites(page, copy);
    if (copiedSites !== 8) fail("the duplicated scene is not the 8-site cell (" + copiedSites + ")");

    await page.locator("#operation-panel-toggle").click();
    await page.waitForFunction(() => {
      const pane = document.getElementById("operation-panel-content");
      return pane && !pane.classList.contains("analysis-tab-content--hidden");
    });
    await setCounts(page, {a: 2, b: 1, c: 1});
    const firstStatus = await commit(page);
    const first = firstStatus.match(/in scene (scene_[0-9a-f]{12})/);
    if (!first) fail("first commit did not name a result scene: " + firstStatus);
    await page.waitForFunction((id) => {
      const marked = document.querySelector("#scene-tabs .tab--selected, #scene-tabs [aria-selected='true']");
      const owned = marked && (marked.closest("[id^='scene-tab-']") || marked);
      return owned && owned.id === "scene-tab-" + id;
    }, first[1], { timeout: 20000 });
    if ((await sites(page, first[1])) !== 16) fail("2x1x1 did not select a 16-site scene");

    await setCounts(page, {a: 2, b: 2, c: 2});
    const secondStatus = await commit(page);
    const second = secondStatus.match(/in scene (scene_[0-9a-f]{12})/);
    if (!second) fail("second commit did not name a result scene: " + secondStatus);
    if (second[1] === first[1]) fail("2x2x2 stayed on the 16-site scene");
    await page.waitForFunction((id) => {
      const marked = document.querySelector("#scene-tabs .tab--selected, #scene-tabs [aria-selected='true']");
      const owned = marked && (marked.closest("[id^='scene-tab-']") || marked);
      return owned && owned.id === "scene-tab-" + id;
    }, second[1], { timeout: 20000 });
    if ((await sites(page, second[1])) !== 128) fail("2x2x2 did not select a 128-site scene");
    if (!/from revision 2/.test(secondStatus)) fail("second status hid the operand: " + secondStatus);

    await page.locator("#scene-tab-" + copy).click();
    await page.waitForFunction((id) => {
      const marked = document.querySelector("#scene-tabs .tab--selected, #scene-tabs [aria-selected='true']");
      const owned = marked && (marked.closest("[id^='scene-tab-']") || marked);
      return owned && owned.id === "scene-tab-" + id;
    }, copy, { timeout: 20000 });
    const refused = await commit(page);
    if (!/was not committed/.test(refused) || !/this view is revision 1/.test(refused)) {
      fail("the old 8-site view committed silently: " + refused);
    }
    if ((await other.evaluate(selectedScene)) !== otherBefore) {
      fail("another page moved when this page committed");
    }
    console.log(JSON.stringify({
      copy,
      first: first[1],
      second: second[1],
      other: otherBefore,
      refused,
    }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
