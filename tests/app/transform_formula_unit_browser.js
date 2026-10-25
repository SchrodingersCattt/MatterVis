/* Native transform buttons must retain the selected scope and deliver a
 * visible warning through status -> mirror_legacy_status -> status-banner.
 */
const assert = require("node:assert/strict");

module.exports = async function (page, clickTab, waitForServerState) {
  await clickTab(page, "molecular_water");
  await page.waitForFunction(() => {
    const graph = document.querySelector("#crystal-graph .js-plotly-plot");
    const scene = graph?.layout?.meta?.mattervis_render?.scene_id;
    const controls = document.getElementById("scene-controls-scene");
    return scene && JSON.parse(controls?.textContent || "{}").scene_id === scene;
  });
  const banner = page.locator("#status-banner");
  const scope = page.locator("#display-mode-selector");
  assert.match(await scope.innerText(), /Formula unit/);
  // Transforms move to Display in #233. Find their owning pane so this gate
  // keeps testing the same native controls before and after that integration.
  const pane = await page.locator("#transforms-add-btn").evaluate(
    node => node.closest('[role="tabpanel"]').id.replace("-content", "-toggle")
  );
  await page.locator("#" + pane).click();

  async function checkRepeat(n) {
    await waitForServerState(page, async (repeat) => {
      const state = await fetch("/api/v2/state", {cache: "no-store"}).then(r => r.json());
      return state.display_mode === "formula_unit" && state.transforms?.length === 1
        && ["a", "b", "c"].every(axis => state.transforms[0].params[axis] === repeat);
    }, n, {timeout: 30000});
    await page.waitForFunction(() => {
      const status = document.getElementById("status-banner");
      return status.textContent.includes("formula_unit will trim")
        && status.classList.contains("status-banner--warning");
    });
    assert.equal(await banner.isVisible(), true, "trim warning is hidden by the active tool pane");
    assert.match(await banner.innerText(), /unit_cell/);
    assert.match(await scope.innerText(), /Formula unit/);
  }

  await page.locator("#transforms-add-btn").click();
  await checkRepeat(1);
  await page.locator("#transforms-preset-2x").click();
  await checkRepeat(2);
  // An identical warning must be delivered again after its dismiss timer.
  await page.waitForFunction(() => !document.getElementById("status-banner").textContent,
    null, {timeout: 15000});
  await page.locator("#transforms-preset-3x").click();
  await checkRepeat(3);
  await page.locator("#analysis-panel-toggle").click();
  assert.equal(await banner.isVisible(), true, "warning disappeared on a tool-pane switch");

  await page.reload({waitUntil: "domcontentloaded"});
  await page.waitForFunction(() => /Formula unit/.test(
    document.getElementById("display-mode-selector")?.textContent || ""
  ));
  await waitForServerState(page, async () => {
    const state = await fetch("/api/v2/state", {cache: "no-store"}).then(r => r.json());
    return state.display_mode === "formula_unit" && state.transforms?.length === 1
      && state.transforms[0].params.a === 3;
  }, null, {timeout: 30000});

  // Explicit scope changes remain available and survive subsequent presets.
  await scope.click();
  await page.getByRole("option", {name: "Unit cell", exact: true}).click();
  await waitForServerState(page, async () => (
    await fetch("/api/v2/state", {cache: "no-store"}).then(r => r.json())
  ).display_mode === "unit_cell", null, {timeout: 30000});
  await page.locator("#" + pane).click();
  await page.locator("#transforms-preset-2x").click();
  await waitForServerState(page, async () => {
    const state = await fetch("/api/v2/state", {cache: "no-store"}).then(r => r.json());
    return state.display_mode === "unit_cell" && state.transforms?.length === 1
      && state.transforms[0].params.a === 2;
  }, null, {timeout: 30000});
  assert.match(await scope.innerText(), /Unit cell/);
  assert.doesNotMatch(await banner.innerText(), /formula_unit will trim/);
};
