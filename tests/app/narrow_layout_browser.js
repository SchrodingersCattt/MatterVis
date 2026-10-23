/* Item (19): exercise the served Dash tree, including callbacks and CSS. */
const assert = require("node:assert/strict");

async function setToolsOpen(page, open) {
  const toggle = page.locator("#compact-toggle-left-panel");
  if ((await toggle.getAttribute("aria-expanded")) !== String(open)) await toggle.click();
  await page.waitForFunction((wantOpen) => {
    const box = document.getElementById("left-panel").getBoundingClientRect();
    return wantOpen ? Math.abs(box.left) < 1 : box.right <= 1;
  }, open);
}

async function checkLogPill(page, width, label) {
  await page.waitForFunction((expected) =>
    getComputedStyle(document.getElementById("perf-log-panel")).width === `${expected}px`,
  width < 756 ? 118 : 128);
  const pill = await page.evaluate(() => {
    const toggle = document.getElementById("perf-log-toggle");
    const panel = document.getElementById("perf-log-panel");
    const box = toggle.getBoundingClientRect();
    const style = getComputedStyle(toggle);
    const text = document.createRange();
    text.selectNodeContents(toggle);
    const textBox = text.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return {
      parent: panel.parentElement.id,
      hit: hit === toggle || toggle.contains(hit),
      inside: box.left >= 0 && box.top >= 0 && box.right <= innerWidth && box.bottom <= innerHeight,
      textFits: textBox.left >= box.left + parseFloat(style.paddingLeft)
        && textBox.right <= box.right - parseFloat(style.paddingRight),
    };
  });
  assert.equal(pill.parent, "center-panel", `${label}: test must use the served log placement`);
  assert(pill.inside && pill.hit, `${label}: log toggle must be visible and clickable`);
  assert(pill.textFits, `${label}: the full Server log label must fit in the collapsed pill`);
  assert(await page.locator("#perf-log-clear").isHidden(), `${label}: Clear belongs in the expanded log`);
  await page.locator("#perf-log-toggle").click();
  await page.locator("#perf-log-panel.perf-log-panel--expanded").waitFor();
  assert(await page.locator("#perf-log-clear").isVisible(), `${label}: expanded log must expose Clear`);
  await page.locator("#perf-log-clear").click();
  await page.locator("#perf-log-toggle").click();
  await page.locator("#perf-log-panel.perf-log-panel--collapsed").waitFor();
}

async function checkSceneStrip(page, width) {
  const layout = await page.evaluate(() => {
    const rect = (node) => node.getBoundingClientRect().toJSON();
    const strip = document.getElementById("scene-tabs");
    const tools = rect(document.getElementById("compact-toggle-left-panel"));
    const brand = document.createRange();
    brand.selectNodeContents(document.querySelector(".workbench-brand"));
    const title = brand.getBoundingClientRect().toJSON();
    return {
      tools, title, strip: rect(strip), drawer: rect(document.getElementById("left-panel")),
      tabs: Array.from(strip.querySelectorAll(".tab"), rect),
    };
  });
  const overlaps = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  assert(!overlaps(layout.tools, layout.title), `${width}: Tools overlaps the MatterVis title`);
  assert(layout.tabs.length >= 4, "regression needs several real scene tabs");
  assert(layout.tabs.every((tab) => Math.abs(tab.top - layout.tabs[0].top) < 1
    && tab.height <= layout.strip.height + 1), `${width}: tabs must stay in one row`);
  assert(layout.strip.left >= layout.drawer.left && layout.strip.right <= layout.drawer.right,
    `${width}: scene strip must stay inside the drawer`);

  // Use the arrows a user sees, rather than assigning scrollLeft or forcing
  // clicks on clipped tabs. Reach both ends and check the end labels fit.
  for (const direction of ["next", "prev"]) {
    const arrow = page.locator(`#scene-tab-scroll-${direction}`);
    for (let step = 0; step < 20; step++) {
      // Scroll events update arrow visibility after the position changes.
      // Read both together before deciding whether another click is needed.
      await page.waitForFunction(() => {
        const strip = document.getElementById("scene-tabs");
        return document.getElementById("scene-tab-scroll-prev").hidden === (strip.scrollLeft <= 1)
          && document.getElementById("scene-tab-scroll-next").hidden
            === (strip.scrollLeft >= strip.scrollWidth - strip.clientWidth - 1);
      });
      if (await arrow.isHidden()) break;
      const before = await page.locator("#scene-tabs").evaluate((strip) => strip.scrollLeft);
      await arrow.click();
      await page.waitForFunction((previous) =>
        Math.abs(document.getElementById("scene-tabs").scrollLeft - previous) > 1, before);
    }
    assert(await arrow.isHidden(), `${width}: ${direction} arrow must reach the end of the tab strip`);
    const endFits = await page.evaluate((edge) => {
      const strip = document.getElementById("scene-tabs");
      const labels = strip.querySelectorAll(".scene-tab-label");
      const box = labels[edge === "prev" ? 0 : labels.length - 1].getBoundingClientRect();
      const host = strip.getBoundingClientRect();
      return box.left >= host.left - 1 && box.right <= host.right + 1;
    }, direction);
    assert(endFits, `${width}: ${direction} end tab stays clipped after scrolling`);
  }
}

module.exports = async function runNarrowLayout(page, clickTab) {
  for (const width of [320, 375, 600, 755]) {
    await page.setViewportSize({width, height: 700});
    await page.locator("#viewer-root.compact-layout").waitFor();
    await setToolsOpen(page, false);
    await checkLogPill(page, width, `${width} Tools closed`);
    await setToolsOpen(page, true);
    await checkSceneStrip(page, width);
    await checkLogPill(page, width, `${width} Tools open`);
    if (width === 375) {
      await clickTab(page, "ionic_nacl");
      await clickTab(page, "DAP-4");
    }
    await setToolsOpen(page, false);
  }
  await page.setViewportSize({width: 1280, height: 800});
  await page.waitForFunction(() => !document.getElementById("viewer-root").classList.contains("compact-layout"));
  assert(await page.locator("#compact-toggle-left-panel").isHidden(), "desktop must restore the sidebar");
  await checkLogPill(page, 1280, "desktop");
};
