"use strict";

const {chromium} = require(process.argv[2]);
const plotlyPath = process.argv[3];
const workbenchCss = process.argv[4];
const panelCss = process.argv[5];
const panelJs = process.argv[6];
const shotDir = process.argv[7];

const DASH_CSS = `
.tab-parent { display: flex; flex-direction: column; overflow: hidden; }
.tab-container { display: flex; flex-direction: column; }
.tab {
  flex: 1 1 0;
  min-width: 0;
  padding: 20px 25px;
  box-sizing: border-box;
  border: 1px solid #d6d6d6;
}
.tab-parent--above-breakpoint .tab-container { flex-direction: row; }
.dash-dropdown { display: block; width: 100%; overflow: hidden; box-sizing: border-box; }
.dash-dropdown-grid-container {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: 8px;
}
.dash-dropdown-grid-container:has(> :nth-child(3)) { grid-template-columns: auto 1fr auto; }
.dash-dropdown-value {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dash-dropdown-trigger { min-height: 32px; padding: 0 12px; }
`;

function tab(name) {
  return `<div class="tab" id="scene-tab-${name.replace(/\\s+/g, "-")}">
    <span class="scene-tab-label" title="${name}">
      <span class="scene-tab-text" title="${name}">${name}</span>
      <button class="tab-close-x" type="button">×</button>
    </span>
  </div>`;
}

const NAMES = [
  "DAP-4",
  "ionic NaCl",
  "water",
  "porous framework",
  "showcase",
  "benzene",
  "orthorhombic",
  "formula unit",
];

const PAGE = `<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body { margin: 0; height: 100%; }
  ${DASH_CSS}
</style></head>
<body>
<div id="viewer-root" style="display:flex;height:100vh;overflow:hidden;background:#fff">
  <div id="left-panel" style="width:380px;min-width:0;flex:0 0 auto;height:100vh;overflow:auto;box-sizing:border-box">
    <h3 class="workbench-brand">MatterVis</h3>
    <div class="scene-header">
      <label>Scenes</label>
      <div class="scene-actions">
        <button class="scene-batch-close-btn" type="button">Close others</button>
        <button class="scene-undo-close-btn" type="button">Undo close</button>
        <button id="scene-new-tab-btn" class="scene-new-tab-btn" type="button">+ Duplicate</button>
      </div>
    </div>
    <div class="scene-tabs-card">
      <div id="scene-tabs-parent" class="tab-parent scene-tabs">
        <div id="scene-tabs" class="tab-container">${NAMES.map(tab).join("")}</div>
      </div>
    </div>
    <label>Display Scope</label>
    <button class="dash-dropdown" type="button" id="scope-dropdown">
      <span class="dash-dropdown-grid-container dash-dropdown-trigger">
        <span class="dash-dropdown-value">Formula unit cluster</span>
        <span class="dash-dropdown-trigger-icon">▾</span>
      </span>
    </button>
    <div class="mv-style-row">
      <button class="dash-dropdown mv-style-dropdown" type="button">
        <span class="dash-dropdown-grid-container dash-dropdown-trigger">
          <span class="dash-dropdown-value" data-label="mesh">3D Mesh</span>
          <span class="dash-dropdown-trigger-icon">▾</span>
        </span>
      </button>
      <button class="dash-dropdown mv-style-dropdown" type="button">
        <span class="dash-dropdown-grid-container dash-dropdown-trigger">
          <span class="dash-dropdown-value" data-label="style">Ball-stick</span>
          <span class="dash-dropdown-trigger-icon">▾</span>
        </span>
      </button>
      <button class="dash-dropdown mv-style-dropdown" type="button">
        <span class="dash-dropdown-grid-container dash-dropdown-trigger">
          <span class="dash-dropdown-value" data-label="disorder">Outline rings</span>
          <span class="dash-dropdown-trigger-icon">▾</span>
        </span>
      </button>
    </div>
  </div>
  <div id="left-splitter" class="panel-splitter"></div>
  <div id="center-panel" style="position:relative;flex:1 1 auto;min-width:0;min-height:0;height:100vh;overflow:hidden">
    <div id="crystal-graph" style="width:100%;height:100%"></div>
  </div>
  <div id="perf-log-panel" class="perf-log-panel perf-log-panel--collapsed">
    <div class="perf-log-header">
      <button id="perf-log-toggle" class="perf-log-toggle" type="button">Server log ▾</button>
      <button id="perf-log-clear" class="perf-log-clear" type="button">Clear</button>
    </div>
  </div>
</div>
</body></html>`;

function overlaps(a, b) {
  if (!a || !b || a.width < 1 || b.width < 1) return false;
  return a.left < b.right - 1 && a.right > b.left + 1 && a.top < b.bottom - 1 && a.bottom > b.top + 1;
}

async function readLayout(page) {
  return page.evaluate(() => {
    const rect = (node) => {
      if (!node) return null;
      const box = node.getBoundingClientRect();
      return {
        left: box.left, top: box.top, right: box.right, bottom: box.bottom,
        width: box.width, height: box.height,
      };
    };
    const root = document.getElementById("viewer-root");
    const left = document.getElementById("left-panel");
    const center = document.getElementById("center-panel");
    const strip = document.getElementById("scene-tabs");
    const tabs = Array.from(strip.querySelectorAll(":scope > .tab"));
    const plot = document.querySelector("#crystal-graph .js-plotly-plot, #crystal-graph.js-plotly-plot");
    const full = plot && plot._fullLayout;
    const canvas = plot && plot.querySelector("canvas");
    const svg = plot && plot.querySelector(".svg-container");
    const sceneObj = plot && plot._fullLayout && plot._fullLayout.scene && plot._fullLayout.scene._scene;
    const glplot = sceneObj && (sceneObj.glplot || sceneObj.scene);
    const gl = glplot && glplot.gl;
    let glViewport = null;
    try {
      if (gl && gl.VIEWPORT) glViewport = Array.from(gl.getParameter(gl.VIEWPORT));
    } catch (err) {}
    const labels = {};
    document.querySelectorAll(".dash-dropdown-value").forEach((node) => {
      const key = node.getAttribute("data-label") || node.textContent;
      const box = node.getBoundingClientRect();
      labels[key] = {text: node.textContent, width: box.width, scroll: node.scrollWidth};
    });
    const rootBox = root.getBoundingClientRect();
    const leftBox = left.getBoundingClientRect();
    return {
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      dpr: window.devicePixelRatio,
      compact: root.classList.contains("compact-layout"),
      panelRatio: leftBox.width / rootBox.width,
      left: rect(left),
      center: rect(center),
      tools: rect(document.getElementById("compact-toggle-left-panel")),
      duplicate: rect(document.getElementById("scene-new-tab-btn")),
      closeOthers: rect(document.querySelector(".scene-batch-close-btn")),
      undo: rect(document.querySelector(".scene-undo-close-btn")),
      strip: {
        clientWidth: strip.clientWidth,
        scrollWidth: strip.scrollWidth,
        scrollHeight: strip.scrollHeight,
        rect: rect(strip),
      },
      tabs: tabs.map((tab) => {
        const box = tab.getBoundingClientRect();
        const text = tab.querySelector(".scene-tab-text");
        const textBox = text.getBoundingClientRect();
        return {
          name: text.textContent,
          width: box.width,
          height: box.height,
          left: box.left,
          right: box.right,
          top: box.top,
          bottom: box.bottom,
          textWidth: textBox.width,
          textScroll: text.scrollWidth,
        };
      }),
      labels,
      plot: full ? {width: full.width, height: full.height} : null,
      gl: canvas && glplot ? {
        buffer: [canvas.width, canvas.height],
        style: [canvas.style.width, canvas.style.height],
        pixelRatio: glplot.pixelRatio,
        viewport: glViewport,
      } : null,
      canvas: rect(canvas),
      svg: rect(svg),
      host: {width: center.clientWidth, height: center.clientHeight},
    };
  });
}

function assertLayout(name, sample, checks) {
  const failures = [];
  checks.forEach((check) => {
    if (!check.ok) failures.push(name + ": " + check.message);
  });
  return failures;
}

function canvasChecks(sample) {
  const host = sample.host;
  const drawn = sample.svg || sample.canvas;
  const failures = [];
  if (!sample.plot) failures.push("plot has no layout size");
  else {
    if (Math.abs(sample.plot.width - host.width) > 4) {
      failures.push(`plot width ${sample.plot.width} != panel ${host.width}`);
    }
    if (Math.abs(sample.plot.height - host.height) > 4) {
      failures.push(`plot height ${sample.plot.height} != panel ${host.height}`);
    }
  }
  if (!drawn) failures.push("missing plot canvas");
  else {
    const widthRatio = drawn.width / sample.center.width;
    const heightRatio = drawn.height / sample.center.height;
    if (widthRatio < 0.9) failures.push(`plot fills ${widthRatio.toFixed(2)} of the panel width`);
    if (heightRatio < 0.9) failures.push(`plot fills ${heightRatio.toFixed(2)} of the panel height`);
    if (drawn.width > sample.center.width + 4) failures.push("plot wider than the panel");
    if (Math.abs(drawn.left - sample.center.left) > 8) {
      failures.push(`plot left gap ${Math.round(drawn.left - sample.center.left)}`);
    }
  }
  return failures;
}

function tabChecks(sample) {
  const failures = [];
  const strip = sample.strip;
  if (!(strip.scrollWidth > strip.clientWidth + 24)) {
    failures.push(`tab strip does not scroll (${strip.scrollWidth} vs ${strip.clientWidth})`);
  }
  if (strip.scrollHeight > 72) {
    failures.push(`tab strip is ${Math.round(strip.scrollHeight)}px tall`);
  }
  sample.tabs.forEach((tab) => {
    if (tab.height > 56) failures.push(`${tab.name} tab is ${Math.round(tab.height)}px tall`);
    if (tab.textWidth < 28) failures.push(`${tab.name} text is only ${Math.round(tab.textWidth)}px wide`);
  });
  const first = sample.tabs[0];
  if (first && first.textScroll > first.textWidth + 4) {
    failures.push(`${first.name} is truncated inside the chip`);
  }
  return failures;
}

const zlib = require("node:zlib");

function decodePng(buf) {
  let offset = 8;
  let width = 0;
  let height = 0;
  let colorType = 0;
  const idat = [];
  while (offset + 8 <= buf.length) {
    const length = buf.readUInt32BE(offset);
    offset += 4;
    const type = buf.toString("ascii", offset, offset + 4);
    offset += 4;
    const data = buf.subarray(offset, offset + length);
    offset += length + 4;
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      colorType = data[9];
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      break;
    }
  }
  const channels = colorType === 6 ? 4 : colorType === 2 ? 3 : 0;
  if (!channels) throw new Error("unsupported screenshot png");
  const inflated = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const pixels = Buffer.alloc(height * stride);
  let src = 0;
  let prev = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const filter = inflated[src++];
    const row = inflated.subarray(src, src + stride);
    src += stride;
    const cur = Buffer.alloc(stride);
    for (let i = 0; i < stride; i++) {
      const left = i >= channels ? cur[i - channels] : 0;
      const up = prev[i];
      const ul = i >= channels ? prev[i - channels] : 0;
      let value = row[i];
      if (filter === 1) value += left;
      else if (filter === 2) value += up;
      else if (filter === 3) value += (left + up) >> 1;
      else if (filter === 4) {
        const paeth = left + up - ul;
        const pa = Math.abs(paeth - left);
        const pb = Math.abs(paeth - up);
        const pc = Math.abs(paeth - ul);
        value += pa <= pb && pa <= pc ? left : pb <= pc ? up : ul;
      }
      cur[i] = value & 255;
    }
    cur.copy(pixels, y * stride);
    prev = cur;
  }
  return {width, height, channels, pixels};
}

function markerPixel(r, g, b) {
  return g > 50 && r < 90 && b < 170 && g > r + 25 && g + 20 > b;
}

function markerPlacement(pngBuffer, sample) {
  const png = decodePng(pngBuffer);
  const scaleX = png.width / sample.innerWidth;
  const scaleY = png.height / sample.innerHeight;
  const canvas = sample.canvas;
  if (!canvas) return {count: 0};
  const x0 = Math.max(0, Math.floor(canvas.left * scaleX));
  const y0 = Math.max(0, Math.floor(canvas.top * scaleY));
  const x1 = Math.min(png.width, Math.ceil((canvas.left + canvas.width) * scaleX));
  const y1 = Math.min(png.height, Math.ceil((canvas.top + canvas.height) * scaleY));
  let count = 0;
  let sumX = 0;
  let sumY = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const index = (y * png.width + x) * png.channels;
      if (!markerPixel(png.pixels[index], png.pixels[index + 1], png.pixels[index + 2])) continue;
      count += 1;
      sumX += x;
      sumY += y;
    }
  }
  if (!count) return {count: 0, image: [png.width, png.height]};
  return {
    count,
    image: [png.width, png.height],
    cx: (sumX / count) / scaleX,
    cy: (sumY / count) / scaleY,
  };
}

function markerChecks(name, sample, marker) {
  const failures = [];
  const canvas = sample.canvas;
  if (!marker || marker.count < 30) {
    failures.push(`${name} structure is not drawn (${marker ? marker.count : 0} marker pixels)`);
    return failures;
  }
  const dx = Math.abs(marker.cx - (canvas.left + canvas.width / 2));
  const dy = Math.abs(marker.cy - (canvas.top + canvas.height / 2));
  if (dx > canvas.width * 0.12) {
    failures.push(`${name} structure is ${Math.round(dx)}px off horizontal center`);
  }
  if (dy > canvas.height * 0.12) {
    failures.push(`${name} structure is ${Math.round(dy)}px off vertical center`);
  }
  return failures;
}

function labelChecks(sample) {
  const failures = [];
  ["3D Mesh", "Ball-stick", "Outline rings", "Formula unit cluster"].forEach((name) => {
    const label = sample.labels[name] || sample.labels[{
      "3D Mesh": "mesh",
      "Ball-stick": "style",
      "Outline rings": "disorder",
    }[name]];
    if (!label) {
      failures.push(`missing dropdown ${name}`);
      return;
    }
    if (label.width < 64) failures.push(`${name} is only ${Math.round(label.width)}px wide`);
    if (label.scroll > label.width + 6) failures.push(`${name} is truncated (${Math.round(label.width)} of ${label.scroll})`);
  });
  return failures;
}

(async () => {
  const fs = require("node:fs");
  const path = require("node:path");
  fs.mkdirSync(shotDir, {recursive: true});
  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      executablePath: process.argv[8],
      args: ["--no-sandbox", "--disable-dev-shm-usage", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--enable-webgl"],
    });
  } catch (error) {
    console.error("BROWSER_GATE_UNAVAILABLE: " + error.message);
    process.exitCode = 2;
    return;
  }
  const page = await browser.newPage({viewport: {width: 1280, height: 800}});
  const failures = [];
  const report = {};
  try {
    await page.setContent(PAGE);
    await page.addStyleTag({path: workbenchCss});
    await page.addStyleTag({path: panelCss});
    await page.addScriptTag({path: panelJs});
    await page.addScriptTag({path: plotlyPath});
    await page.evaluate(async () => {
      const host = document.getElementById("crystal-graph");
      await window.Plotly.newPlot(host, [{
        type: "scatter3d",
        mode: "markers",
        x: [-1, 1, 0, 0],
        y: [0, 0, -1, 1],
        z: [0, 0, 0, 0],
        marker: {size: 10, color: "#0f766e"},
      }], {
        margin: {l: 0, r: 0, t: 36, b: 0},
        paper_bgcolor: "#ffffff",
        title: {text: "NaCl"},
        scene: {
          aspectmode: "cube",
          camera: {eye: {x: 1.4, y: 1.4, z: 1.2}, center: {x: 0, y: 0, z: 0}, up: {x: 0, y: 0, z: 1}},
        },
      }, {responsive: true, displayModeBar: false});
    });

    async function settle(label) {
      await page.evaluate(() => {
        window.dispatchEvent(new Event("resize"));
        if (window.MatterVisPanelResize) window.MatterVisPanelResize.fitPanels();
      });
      await page.waitForFunction(() => {
        const plot = document.querySelector(".js-plotly-plot");
        const host = document.getElementById("center-panel");
        return !!(plot && plot._fullLayout && host
          && Math.abs(plot._fullLayout.width - host.clientWidth) < 4
          && Math.abs(plot._fullLayout.height - host.clientHeight) < 4);
      }, null, {timeout: 8000});
      await page.waitForTimeout(120);
      const sample = await readLayout(page);
      report[label] = {
        innerWidth: sample.innerWidth,
        dpr: sample.dpr,
        panelRatio: sample.panelRatio,
        plot: sample.plot,
        host: sample.host,
        canvas: sample.canvas,
        svg: sample.svg,
        gl: sample.gl,
        strip: {scrollWidth: sample.strip.scrollWidth, clientWidth: sample.strip.clientWidth, scrollHeight: sample.strip.scrollHeight},
        compact: sample.compact,
      };
      return sample;
    }

    async function checkMarkers(name, sample) {
      const png = await page.screenshot();
      fs.writeFileSync(path.join(shotDir, `layout-${name}.png`), png);
      const marker = markerPlacement(png, sample);
      if (report[name]) report[name].marker = {count: marker.count, cx: marker.cx, cy: marker.cy, image: marker.image};
      failures.push(...markerChecks(name, sample, marker));
    }

    const desktop = await settle("1280");
    await checkMarkers("1280", desktop);
    failures.push(...assertLayout("1280", desktop, []));
    failures.push(...canvasChecks(desktop).map((item) => "1280 " + item));
    failures.push(...tabChecks(desktop).map((item) => "1280 " + item));
    failures.push(...labelChecks(desktop).map((item) => "1280 " + item));
    if (desktop.panelRatio < 0.2 || desktop.panelRatio > 0.45) {
      failures.push(`1280 panel ratio ${desktop.panelRatio.toFixed(3)}`);
    }
    const arrows = await page.evaluate(() => ({
      prevHidden: document.getElementById("scene-tab-scroll-prev").hidden,
      nextHidden: document.getElementById("scene-tab-scroll-next").hidden,
    }));
    if (arrows.nextHidden) failures.push("scene tabs overflow without a next arrow");
    if (!arrows.prevHidden) failures.push("previous arrow is visible before the strip scrolls");
    await page.click("#scene-tab-scroll-next");
    const moved = await page.evaluate(() => document.getElementById("scene-tabs").scrollLeft);
    if (moved < 40) failures.push(`next arrow did not scroll the strip (${moved})`);
    await page.evaluate(() => {
      const current = document.getElementById("scene-tabs");
      let staleScrolls = 0;
      const original = current.scrollBy.bind(current);
      current.scrollBy = function () {
        staleScrolls += 1;
        return original.apply(current, arguments);
      };
      window.__staleTabScrolls = () => staleScrolls;
      const replacement = current.cloneNode(true);
      replacement.scrollLeft = 0;
      current.replaceWith(replacement);
    });
    await page.click("#scene-tab-scroll-next");
    const replaced = await page.evaluate(() => ({
      live: document.getElementById("scene-tabs").scrollLeft,
      stale: window.__staleTabScrolls(),
    }));
    if (replaced.live < 40) {
      failures.push(`replaced scene strip did not scroll (${replaced.live})`);
    }
    if (replaced.stale) {
      failures.push(`next arrow scrolled the detached strip (${replaced.stale})`);
    }
    await page.evaluate(() => {
      const strip = document.getElementById("scene-tabs");
      strip.scrollLeft = strip.scrollWidth;
    });
    const scrolled = await readLayout(page);
    const last = scrolled.tabs[scrolled.tabs.length - 1];
    if (last.right > scrolled.strip.rect.right + 2 || last.left < scrolled.strip.rect.left - 2) {
      failures.push(`last tab stays clipped after scroll (${Math.round(last.left)}..${Math.round(last.right)} vs strip ${Math.round(scrolled.strip.rect.left)}..${Math.round(scrolled.strip.rect.right)})`);
    }

    await page.setViewportSize({width: 900, height: 700});
    const narrowed = await settle("900");
    failures.push(...canvasChecks(narrowed).map((item) => "900 " + item));
    if (narrowed.compact) failures.push("900px was treated as the phone layout");

    const client = await page.context().newCDPSession(page);
    async function zoom(label, cssWidth, cssHeight, scale) {
      // The window has to match the emulated CSS viewport. Overriding device
      // metrics on a smaller window paints only that window and makes a
      // centered WebGL canvas look clipped in the top-left of the screenshot.
      await page.setViewportSize({width: cssWidth, height: cssHeight});
      await client.send("Emulation.setDeviceMetricsOverride", {
        width: cssWidth,
        height: cssHeight,
        deviceScaleFactor: scale,
        mobile: false,
      });
      const sample = await settle(label);
      await checkMarkers(label, sample);
      failures.push(...canvasChecks(sample).map((item) => label + " " + item));
      failures.push(...labelChecks(sample).map((item) => label + " " + item));
      if (sample.compact) failures.push(label + " unexpectedly used the compact drawer");
      if (sample.panelRatio < 0.2 || sample.panelRatio > 0.45) {
        failures.push(`${label} left panel ratio ${sample.panelRatio.toFixed(3)} (dpr ${sample.dpr})`);
      }
      return sample;
    }
    await zoom("zoom-50", 2560, 1600, 0.5);
    await zoom("zoom-33", 3840, 2400, 0.33);
    await client.send("Emulation.clearDeviceMetricsOverride");

    await page.setViewportSize({width: 375, height: 700});
    await settle("375");
    await page.click("#compact-toggle-left-panel");
    await page.waitForTimeout(200);
    const narrow = await readLayout(page);
    report["375-open"] = {
      innerWidth: narrow.innerWidth,
      compact: narrow.compact,
      tools: narrow.tools,
      duplicate: narrow.duplicate,
      strip: narrow.strip,
      tabHeights: narrow.tabs.map((tab) => Math.round(tab.height)),
    };
    await page.screenshot({path: path.join(shotDir, "layout-375-tools.png")});
    if (!narrow.compact) failures.push("375px did not open the compact layout");
    if (overlaps(narrow.tools, narrow.duplicate)) failures.push("Tools button overlaps Duplicate");
    if (overlaps(narrow.tools, narrow.closeOthers)) failures.push("Tools button overlaps Close others");
    if (overlaps(narrow.tools, narrow.undo)) failures.push("Tools button overlaps Undo close");
    if (overlaps(narrow.closeOthers, narrow.undo) || overlaps(narrow.undo, narrow.duplicate) || overlaps(narrow.closeOthers, narrow.duplicate)) {
      failures.push("scene header buttons overlap each other");
    }
    failures.push(...tabChecks(narrow).map((item) => "375 " + item));
    narrow.tabs.forEach((tab) => {
      if (tab.bottom && tab.bottom > narrow.left.bottom + 1 && tab.top < narrow.left.bottom) {
        failures.push(`${tab.name} runs past the drawer`);
      }
    });
    if (narrow.strip.rect.right > narrow.left.right + 2) {
      failures.push("tab strip runs past the drawer");
    }
    failures.push(...labelChecks(narrow).map((item) => "375 " + item));
    const logPill = await page.evaluate(() => {
      const node = document.getElementById("perf-log-panel");
      const box = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      const hit = document.elementFromPoint(
        Math.min(box.right - 4, box.left + box.width / 2),
        box.top + box.height / 2,
      );
      return {
        left: box.left, top: box.top, right: box.right, bottom: box.bottom,
        width: box.width, height: box.height,
        z: style.zIndex,
        text: (node.querySelector(".perf-log-toggle") || {}).textContent || "",
        shown: style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0,
        hit: !!(hit && node.contains(hit)),
        hitId: hit && hit.id,
      };
    });
    report["375-open"].logPill = logPill;
    if (!logPill.shown) failures.push("log pill is hidden at 375px");
    if (logPill.width < 80 || logPill.height < 20) {
      failures.push(`log pill is ${Math.round(logPill.width)}×${Math.round(logPill.height)}`);
    }
    if (logPill.left < 0 || logPill.top < 0 || logPill.right > narrow.innerWidth + 1 || logPill.bottom > 700 + 1) {
      failures.push(`log pill sits outside the 375px viewport (${Math.round(logPill.left)},${Math.round(logPill.top)} ${Math.round(logPill.right)},${Math.round(logPill.bottom)})`);
    }
    if (!logPill.hit) failures.push(`log pill is covered (${logPill.hitId || "nothing"})`);
    if (!logPill.text.includes("Server log")) failures.push(`log pill label is "${logPill.text}"`);
    if (overlaps(logPill, narrow.tools)) failures.push("log pill overlaps the Tools button");

    fs.writeFileSync(path.join(shotDir, "report.json"), JSON.stringify(report, null, 2));
    if (failures.length) {
      console.error(failures.join("\n"));
      console.error(JSON.stringify(report, null, 2));
      process.exitCode = 1;
    } else {
      console.log(JSON.stringify(report, null, 2));
    }
  } catch (error) {
    console.error(error && error.stack ? error.stack : String(error));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
