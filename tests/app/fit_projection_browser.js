/* Paint Fit at the workbench plot size and at wide and tall windows.
 * Args: playwright module, chrome path, base url.
 */
const fs = require("node:fs");
const { chromium } = require(process.argv[2]);
const chrome = process.argv[3];
const base = process.argv[4];
const outDir = process.argv[5];

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

async function painted(page) {
  return page.evaluate(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const layout = gd && gd._fullLayout && gd._fullLayout.scene;
    const live = layout && layout._scene;
    const gl = live && (live.camera || (live.glplot && live.glplot.camera));
    const rect = gd.getBoundingClientRect();
    function vec(value) {
      if (!value) return null;
      const x = value.x !== undefined ? Number(value.x) : Number(value[0]);
      const y = value.y !== undefined ? Number(value.y) : Number(value[1]);
      const z = value.z !== undefined ? Number(value.z) : Number(value[2]);
      return [x, y, z];
    }
    return {
      width: rect.width,
      height: rect.height,
      eye: vec(gl && gl.eye),
      center: vec(gl && gl.center),
      up: vec(gl && gl.up),
      projection: gl && gl._ortho ? "orthographic" : "perspective",
      aspect: layout && layout.aspectratio ? {x: layout.aspectratio.x, y: layout.aspectratio.y, z: layout.aspectratio.z} : null,
      ranges: layout && {
        x: layout.xaxis.range,
        y: layout.yaxis.range,
        z: layout.zaxis.range,
      },
    };
  });
}

function liveSample() {
  const gd = document.querySelector("#crystal-graph .js-plotly-plot");
  const layout = gd && gd._fullLayout && gd._fullLayout.scene;
  const gl = layout && layout._scene && (layout._scene.camera || (layout._scene.glplot && layout._scene.glplot.camera));
  const render = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
  const aspect = layout && layout.aspectratio;
  function vec(value) {
    if (!value) return null;
    const x = value.x !== undefined ? Number(value.x) : Number(value[0]);
    const y = value.y !== undefined ? Number(value.y) : Number(value[1]);
    const z = value.z !== undefined ? Number(value.z) : Number(value[2]);
    return [x, y, z];
  }
  const span = layout && layout.xaxis && layout.xaxis.range ? Math.abs(layout.xaxis.range[1] - layout.xaxis.range[0]) : null;
  const status = document.getElementById("mv-render-status");
  const boundary = document.querySelector("#display-options input[value='unit_cell_box']");
  return {
    projection: gl && gl._ortho ? "orthographic" : "perspective",
    eye: vec(gl && gl.eye),
    center: vec(gl && gl.center),
    aspect: aspect ? {x: Number(aspect.x), y: Number(aspect.y), z: Number(aspect.z)} : null,
    span: span,
    ranges: layout && layout.xaxis ? {x: layout.xaxis.range, y: layout.yaxis.range, z: layout.zaxis.range} : null,
    revision: render ? Number(render.camera_revision) : null,
    renderRevision: render ? Number(render.render_revision) : null,
    sceneId: render ? render.scene_id : null,
    status: status && !status.hidden ? String(status.textContent || "") : "",
    postcondition: (window.__mv_camera_diag && window.__mv_camera_diag.lastPostcondition) || null,
    boundaryChecked: !!(boundary && boundary.checked),
    aspectMode: layout && layout.aspectmode,
    userAspect: gd.layout && gd.layout.scene && gd.layout.scene.aspectratio ? {x: gd.layout.scene.aspectratio.x, y: gd.layout.scene.aspectratio.y, z: gd.layout.scene.aspectratio.z} : null,
    glAspect: (function () {
      try { return gd._fullLayout.scene._scene.glplot.getAspectratio(); } catch (error) { return String(error); }
    })(),
    lastApply: window.__mv_camera_diag && window.__mv_camera_diag.lastApply || null,
  };
}

function readCapture() {
  const gd = document.querySelector("#crystal-graph .js-plotly-plot");
  const layout = gd && gd._fullLayout && gd._fullLayout.scene;
  const gl = layout && layout._scene && (layout._scene.camera || (layout._scene.glplot && layout._scene.glplot.camera));
  const render = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
  const aspect = layout && layout.aspectratio;
  function vec(value) {
    if (!value) return null;
    const x = value.x !== undefined ? Number(value.x) : Number(value[0]);
    const y = value.y !== undefined ? Number(value.y) : Number(value[1]);
    const z = value.z !== undefined ? Number(value.z) : Number(value[2]);
    return [x, y, z];
  }
  const status = document.getElementById("mv-render-status");
  const boundary = document.querySelector("#display-options input[value='unit_cell_box']");
  const sample = {
    projection: gl && gl._ortho ? "orthographic" : "perspective",
    eye: vec(gl && gl.eye),
    center: vec(gl && gl.center),
    aspect: aspect ? {x: Number(aspect.x), y: Number(aspect.y), z: Number(aspect.z)} : null,
    span: layout && layout.xaxis && layout.xaxis.range ? Math.abs(layout.xaxis.range[1] - layout.xaxis.range[0]) : null,
    ranges: layout && layout.xaxis ? {x: layout.xaxis.range, y: layout.yaxis.range, z: layout.zaxis.range} : null,
    revision: render ? Number(render.camera_revision) : null,
    renderRevision: render ? Number(render.render_revision) : null,
    sceneId: render ? render.scene_id : null,
    status: status && !status.hidden ? String(status.textContent || "") : "",
    postcondition: (window.__mv_camera_diag && window.__mv_camera_diag.lastPostcondition) || null,
    boundaryChecked: !!(boundary && boundary.checked),
  };
  const canvas = gd.querySelector("canvas").getBoundingClientRect();
  const bar = document.getElementById("mv-status-bar");
  const controls = document.getElementById("canvas-view-fit");
  const limits = [canvas.bottom];
  if (bar) limits.push(bar.getBoundingClientRect().top);
  if (controls) limits.push(controls.getBoundingClientRect().top);
  const bottom = Math.min.apply(null, limits);
  sample.plot = {x: canvas.x, y: canvas.y, width: canvas.width, height: Math.max(1, bottom - canvas.y)};
  sample.canvas = {x: canvas.x, y: canvas.y, width: canvas.width, height: canvas.height};
  sample.chrome = ["compact-toggle-left-panel", "canvas-view-reset", "canvas-view-fit", "mv-status-bar", "mv-render-status", "perf-log-panel"].map((id) => {
    const node = document.getElementById(id);
    if (!node || node.hidden) return null;
    const rect = node.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return null;
    const pad = id === "perf-log-panel" ? 24 : 0;
    return {id, x: rect.x - pad, y: rect.y - pad, width: rect.width + 2 * pad, height: rect.height + 2 * pad};
  }).filter(Boolean);
  return sample;
}

function sameCapture(left, right) {
  return sameSample(left, right)
    && JSON.stringify(left.plot) === JSON.stringify(right.plot)
    && JSON.stringify(left.canvas) === JSON.stringify(right.canvas)
    && JSON.stringify(left.chrome) === JSON.stringify(right.chrome);
}

function sampleMatches(sample, target) {
  if (!sample || !sample.eye || !sample.aspect || sample.revision === null) return false;
  if (sample.revision < target.revision) return false;
  if (sample.projection !== target.projection) return false;
  const got = Math.hypot(sample.eye[0] - target.eyeVec[0], sample.eye[1] - target.eyeVec[1], sample.eye[2] - target.eyeVec[2]);
  const eyeOk = Number.isFinite(got) && got / Math.max(target.eye, 1e-6) < 0.02;
  const aspectOk = Math.abs(Number(sample.aspect.x) - target.scale) / Math.max(target.scale, 1e-6) < 0.01;
  // The cubic fixture's data box is about 7 across. The default
  // perspective view widens that to about 11. Fit replaces it.
  return eyeOk && aspectOk && sample.span < 9 && sample.boundaryChecked === true;
}

function sameSample(left, right) {
  if (!left || !right) return false;
  return left.revision === right.revision
    && left.sceneId === right.sceneId
    && left.span === right.span
    && left.status === right.status
    && JSON.stringify(left.eye) === JSON.stringify(right.eye)
    && JSON.stringify(left.aspect) === JSON.stringify(right.aspect)
    && JSON.stringify(left.ranges) === JSON.stringify(right.ranges);
}

async function fit(page, projection) {
  const posted = await page.evaluate(async (mode) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const sceneId = gd.layout.meta.mattervis_render.scene_id;
    const response = await fetch("/api/v2/state", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        scene_id: sceneId,
        display_mode: "unit_cell",
        display_options: ["unit_cell_box"],
        projection: mode,
      }),
    });
    const state = await response.json();
    return {revision: Number(state.render_revision || 0), projection: mode};
  }, projection);
  const settleProjection = Date.now() + 20000;
  let previousProjection = null;
  while (Date.now() < settleProjection) {
    const sample = await page.evaluate(liveSample);
    const stable = previousProjection && sameSample(previousProjection, sample);
    if (stable && sample.projection === projection && sample.renderRevision >= posted.revision && sample.boundaryChecked) break;
    previousProjection = sample;
    await new Promise((resolve) => setTimeout(resolve, 50));
    if (Date.now() >= settleProjection) fail("projection did not settle " + JSON.stringify(sample));
  }
  const axesBox = page.locator("#display-options input[value='axes']");
  if (await axesBox.count() && await axesBox.isChecked()) {
    await axesBox.locator("xpath=ancestor::label[1]").click();
    await page.waitForFunction(() => {
      const box = document.querySelector("#display-options input[value='axes']");
      return !box || !box.checked;
    }, null, { timeout: 10000 });
  }
  const beforeClick = await page.evaluate(liveSample);
  const pending = page.waitForResponse((response) => {
    return response.url().includes("/api/v2/camera/action") && response.request().method() === "POST";
  }, { timeout: 20000 });
  await page.locator("#canvas-view-fit").click();
  const action = await (await pending).json();
  const serverEye = action.camera && action.camera.eye;
  const state = await page.evaluate(async () => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const sceneId = gd.layout.meta.mattervis_render.scene_id;
    return fetch("/api/v2/state?scene_id=" + encodeURIComponent(sceneId)).then((response) => response.json());
  });
  const expected = {
    scale: Number(state.camera_aspect_scale || 1),
    revision: Number(state.camera_revision || 0),
    projection: projection,
    eye: Math.hypot(Number(serverEye.x), Number(serverEye.y), Number(serverEye.z)),
    eyeVec: [Number(serverEye.x), Number(serverEye.y), Number(serverEye.z)],
    beforeRevision: beforeClick.revision,
  };
  if (!(expected.revision > expected.beforeRevision)) fail("fit did not advance camera_revision " + JSON.stringify(expected));
  const deadline = Date.now() + 20000;
  let previous = null;
  let last = null;
  let settled = false;
  try {
    while (Date.now() < deadline) {
      last = await page.evaluate(liveSample);
      if (sampleMatches(last, expected) && sameSample(previous, last)) {
        settled = true;
        break;
      }
      previous = last;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    if (!settled) fail("fit did not settle " + JSON.stringify({expected, last}));
  } finally {
    if (!settled) {
      const frame = last || await page.evaluate(liveSample).catch((error) => ({error: String(error)}));
      fs.writeFileSync(`${outDir}/cubic-fit-timeout.json`, JSON.stringify({expected, frame}, null, 2));
      await page.screenshot({path: `${outDir}/cubic-fit-timeout.png`}).catch(() => {});
    }
  }
  const frame = await painted(page);
  frame.serverScale = expected.scale;
  frame.boundaryChecked = true;
  return frame;
}

async function main() {
  const browser = await chromium.launch({
    executablePath: chrome,
    headless: true,
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--ignore-gpu-blocklist"],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const shots = [];
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(String(error)));
  try {
    await page.goto(base, { waitUntil: "domcontentloaded" });
    await graphReady(page);
    const windows = [
      {width: 1280, height: 800},
      {width: 1600, height: 700},
      {width: 700, height: 1000},
    ];
    for (const size of windows) {
      await page.setViewportSize(size);
      await page.waitForFunction((expected) => {
        const plot = document.querySelector("#crystal-graph .js-plotly-plot");
        if (!plot) return false;
        const rect = plot.getBoundingClientRect();
        return rect.width > 200 && rect.height > 200 && Math.abs(rect.height - expected.height) < 80;
      }, size, { timeout: 20000 });
      for (const projection of ["perspective", "orthographic"]) {
        const frame = await fit(page, projection);
        if (frame.projection !== projection) fail("painted projection " + frame.projection);
        if (!(frame.width > 200 && frame.height > 200)) fail("plot size " + frame.width + "x" + frame.height);
        const name = `${size.width}x${size.height}-${projection}.png`;
        await page.evaluate(() => new Promise((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(resolve));
        }));
        const before = await page.evaluate(readCapture);
        await page.screenshot({ path: `${outDir}/${name}` });
        const after = await page.evaluate(readCapture);
        if (!sameCapture(before, after)) fail(name + " shutter moved\n" + JSON.stringify({before, after}));
        shots.push({window: size, file: name, console: consoleErrors.slice(), before, ...after});
      }
    }
    console.log(JSON.stringify(shots));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
