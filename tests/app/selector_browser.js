/* Drive the real atom-group and bond-group dropdowns.
 * Args: playwright module, chrome path, base url.
 */
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.argv[2]);
const chrome = process.argv[3];
const base = process.argv[4];

function fail(message) {
  throw new Error(message);
}

function changed(response, needle) {
  try {
    const payload = response.request().postDataJSON();
    return (payload.changedPropIds || []).some((item) => String(item).includes(needle));
  } catch (_) {
    return false;
  }
}

async function stateOf(page) {
  return page.evaluate(async (url) => {
    const response = await fetch(url + "/api/v2/state", { cache: "no-store" });
    if (!response.ok) throw new Error("state " + response.status);
    return response.json();
  }, base);
}

async function setDashColor(page, locator, value, needle) {
  const response = page.waitForResponse((item) => changed(item, needle), { timeout: 20000 });
  await locator.evaluate((node, next) => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(node, next);
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
  await response;
}

async function chooseOption(page, menuText) {
  const option = page.locator("label.dash-dropdown-option").filter({
    hasText: new RegExp("^" + menuText + "$"),
  });
  await option.waitFor({ state: "visible", timeout: 10000 });
  await option.click();
}

async function openKind(page, container, currentLabel) {
  const button = page.locator(container + " button.dash-dropdown").filter({
    hasText: currentLabel,
  }).first();
  await button.scrollIntoViewIfNeeded();
  await button.click();
}

function traceReport(data, color) {
  const wanted = String(color).toLowerCase();
  const hits = [];
  for (const trace of data || []) {
    const paint = String(trace.color || trace.marker && trace.marker.color || "").toLowerCase();
    if (paint !== wanted) continue;
    const xs = trace.x || [];
    const ys = trace.y || [];
    const zs = trace.z || [];
    let sx = 0;
    let sy = 0;
    let sz = 0;
    const n = Math.min(xs.length, ys.length, zs.length);
    for (let i = 0; i < n; i += 1) {
      sx += Number(xs[i]);
      sy += Number(ys[i]);
      sz += Number(zs[i]);
    }
    hits.push({
      n,
      mean: n ? [sx / n, sy / n, sz / n] : null,
      name: trace.name || trace.type || "",
    });
  }
  return hits;
}

(async () => {
  const browser = await chromium.launch({
    executablePath: chrome,
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const artifacts = process.env.MATTERVIS_BROWSER_ARTIFACTS || "";
  try {
    await page.goto(base + "/", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#crystal-graph .js-plotly-plot", { timeout: 90000 });
    await page.waitForSelector("#atom-groups-add-btn");

    const addAtoms = page.waitForResponse((response) => changed(response, "atom-groups-add-btn"), { timeout: 20000 });
    await page.locator("#atom-groups-add-btn").click();
    await addAtoms;

    await openKind(page, "#atom-groups-rows-container", "all atoms");
    const kindChange = page.waitForResponse((response) => changed(response, "ag-row-kind"), { timeout: 20000 });
    await chooseOption(page, "by element");
    await kindChange;
    await page.waitForTimeout(200);

    const atomPicker = page.locator("#atom-groups-rows-container button.dash-dropdown").filter({
      hasText: "Pick elements",
    });
    if (!(await atomPicker.isVisible())) {
      const text = await page.locator("#atom-groups-rows-container").innerText();
      fail("by-element picker is not visible after choosing it:\n" + text);
    }
    let state = await stateOf(page);
    const pendingAtom = (state.atom_groups || []).find((group) => group.selector && "elements" in group.selector && !(group.selector.elements || []).length);
    if (!pendingAtom) {
      fail("empty by-element rule was not persisted: " + JSON.stringify(state.atom_groups));
    }
    if ((state.atom_groups || []).some((group) => group.selector && group.selector.all && group.id === pendingAtom.id)) {
      fail("empty by-element rule was rewritten to all");
    }

    await atomPicker.click();
    const elementChange = page.waitForResponse((response) => changed(response, "ag-row-elements"), { timeout: 20000 });
    await chooseOption(page, "C");
    await elementChange;
    await page.keyboard.press("Escape");

    const color = page.locator("#atom-groups-rows-container input[type='color']").first();
    await setDashColor(page, color, "#00ffcc", "ag-row-color");
    await page.waitForTimeout(400);

    state = await stateOf(page);
    const carbonRule = (state.atom_groups || []).find((group) => (group.selector.elements || []).includes("C"));
    if (!carbonRule) fail("carbon element rule missing after picker: " + JSON.stringify(state.atom_groups));
    const colors = await page.evaluate(() => {
      const plot = document.querySelector("#crystal-graph .js-plotly-plot");
      return (plot && (plot._fullData || plot.data)) || [];
    });
    const carbonPaint = traceReport(colors, "#00ffcc");
    if (!carbonPaint.length) fail("figure has no #00ffcc trace after colouring carbon");
    const carbonMean = carbonPaint[0].mean;
    // C sits near x=8 or 9.5, y=10. O sits near y=11.4. The override mesh
    // centroid must stay with the carbons.
    if (!carbonMean || carbonMean[1] > 10.6) {
      fail("override colour is not on the carbon atoms: " + JSON.stringify(carbonPaint));
    }
    if (artifacts) {
      fs.mkdirSync(artifacts, { recursive: true });
      await page.screenshot({
        path: path.join(artifacts, "selector-carbon.png"),
        fullPage: true,
      });
    }

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#atom-groups-rows-container button.dash-dropdown", { timeout: 60000 });
    state = await stateOf(page);
    const reloaded = (state.atom_groups || []).find((group) => (group.selector.elements || []).includes("C"));
    if (!reloaded) fail("carbon rule did not survive reload: " + JSON.stringify(state.atom_groups));
    const reloadedText = await page.locator("#atom-groups-rows-container").innerText();
    if (!reloadedText.includes("C")) fail("reloaded atom row does not show C:\n" + reloadedText);

    const removeAtoms = page.waitForResponse((response) => changed(response, "ag-row-delete"), { timeout: 20000 });
    await page.locator("#atom-groups-rows-container button[title='Remove this group rule']").click();
    await removeAtoms;
    state = await stateOf(page);
    if ((state.atom_groups || []).some((group) => group.id === reloaded.id)) {
      fail("removed atom rule is still in state");
    }

    const addBonds = page.waitForResponse((response) => changed(response, "bond-groups-add-btn"), { timeout: 20000 });
    await page.locator("#bond-groups-add-btn").click();
    await addBonds;
    await openKind(page, "#bond-groups-rows-container", "all bonds");
    const bondKind = page.waitForResponse((response) => changed(response, "bg-row-kind"), { timeout: 20000 });
    await chooseOption(page, "between elements");
    await bondKind;
    await page.waitForTimeout(200);
    const bondPicker = page.locator("#bond-groups-rows-container button.dash-dropdown").nth(1);
    if (!(await bondPicker.isVisible())) {
      const text = await page.locator("#bond-groups-rows-container").innerText();
      fail("between-elements picker is not visible:\n" + text);
    }
    state = await stateOf(page);
    const pendingBond = (state.bond_groups || []).find((group) => group.selector && "between_elements" in group.selector);
    if (!pendingBond) fail("empty between-elements rule missing: " + JSON.stringify(state.bond_groups));
    if (pendingBond.selector.all) fail("empty between-elements rule became all: " + JSON.stringify(pendingBond));
    if ((pendingBond.selector.between_elements || []).length !== 0) {
      fail("endpoint list was not empty: " + JSON.stringify(pendingBond));
    }

    await bondPicker.click();
    let bondElements = page.waitForResponse((response) => changed(response, "bg-row-elements"), { timeout: 20000 });
    await chooseOption(page, "C");
    await bondElements;
    await page.keyboard.press("Escape");
    await bondPicker.click();
    bondElements = page.waitForResponse((response) => changed(response, "bg-row-elements"), { timeout: 20000 });
    await chooseOption(page, "O");
    await bondElements;

    await page.keyboard.press("Escape");
    const bondColor = page.locator("#bond-groups-rows-container input[type='color']").first();
    await setDashColor(page, bondColor, "#ff00aa", "bg-row-color");
    await page.waitForTimeout(400);
    state = await stateOf(page);
    const pair = (state.bond_groups || []).find((group) => {
      const values = group.selector.between_elements || [];
      return values.includes("C") && values.includes("O");
    });
    if (!pair) fail("C-O endpoint rule missing: " + JSON.stringify(state.bond_groups));
    const bondTraces = await page.evaluate(() => {
      const plot = document.querySelector("#crystal-graph .js-plotly-plot");
      return (plot && (plot._fullData || plot.data)) || [];
    });
    const bondPaint = traceReport(bondTraces, "#ff00aa");
    if (!bondPaint.length) fail("figure has no #ff00aa trace for the C-O bond rule");
    // C-O runs from (8, 10, 10) to (8, 11.4, 10). C-C stays on y=10.
    // A bond mesh coloured only for C-O has mean y above the carbon row.
    const bondMean = bondPaint[0].mean;
    if (!bondMean || bondMean[1] < 10.4) {
      fail("bond override colour is not on the C-O bond: " + JSON.stringify(bondPaint));
    }
    if (artifacts) {
      fs.mkdirSync(artifacts, { recursive: true });
      await page.screenshot({
        path: path.join(artifacts, "selector-targeting.png"),
        fullPage: true,
      });
    }

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#bond-groups-rows-container", { timeout: 60000 });
    state = await stateOf(page);
    const bondReloaded = (state.bond_groups || []).some((group) => {
      const values = (group.selector && group.selector.between_elements) || [];
      return values.includes("C") && values.includes("O");
    });
    if (!bondReloaded) fail("C-O bond rule did not survive reload: " + JSON.stringify(state.bond_groups));
    const bondText = await page.locator("#bond-groups-rows-container").innerText();
    if (!bondText.includes("C") || !bondText.includes("O")) {
      fail("reloaded bond row does not show both endpoints:\n" + bondText);
    }
    const removeBonds = page.waitForResponse((response) => changed(response, "bg-row-delete"), { timeout: 20000 });
    await page.locator("#bond-groups-rows-container button[title='Remove this bond-group rule']").click();
    await removeBonds;
    state = await stateOf(page);
    if ((state.bond_groups || []).length !== 0) {
      fail("bond rule was not removed: " + JSON.stringify(state.bond_groups));
    }
    console.log("selector controls ok");
  } catch (error) {
    if (artifacts) {
      fs.mkdirSync(artifacts, { recursive: true });
      await page.screenshot({ path: path.join(artifacts, "selector-failure.png"), fullPage: true }).catch(() => {});
    }
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
