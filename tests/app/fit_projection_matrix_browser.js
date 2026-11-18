/* Painted Fit for a structural supercell, a triclinic cell, and water.
 * Args: playwright module, chrome path, base url, screenshot directory.
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

async function showScene(page, sceneId) {
  await page.evaluate(async (id) => {
    await fetch("/api/v2/scenes/active", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({scene_id: id}),
    });
    if (typeof window.MatterVisSelectScene === "function") window.MatterVisSelectScene(id);
    if (window.dash_clientside && window.dash_clientside.set_props) {
      window.dash_clientside.set_props("scene-tabs", {value: id});
    }
  }, sceneId);
  await page.waitForFunction((id) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const render = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    const live = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    return !!(render && render.scene_id === id && live && live.glplot && gd.data && gd.data.length);
  }, sceneId, { timeout: 20000 });
}

async function fit(page, projection, boundary) {
  await page.evaluate(async (mode) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const sceneId = gd.layout.meta.mattervis_render.scene_id;
    await fetch("/api/v2/state", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({scene_id: sceneId, display_mode: "unit_cell", projection: mode}),
    });
  }, projection);
  await page.waitForFunction((mode) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const gl = gd && gd._fullLayout.scene._scene.camera;
    const box = document.querySelector("#display-options input[value='unit_cell_box']");
    return gl && (mode === "orthographic" ? gl._ortho : !gl._ortho) && box && !box.disabled;
  }, projection, { timeout: 20000 });
  async function setChecked(value, want) {
    const box = page.locator(`#display-options input[value='${value}']`);
    if (!(await box.count())) return;
    if ((await box.isChecked()) === want) return;
    await box.locator("xpath=ancestor::label[1]").click();
    await page.waitForFunction(({value, want}) => {
      const node = document.querySelector(`#display-options input[value='${value}']`);
      return !node || node.checked === want;
    }, {value, want}, {timeout: 10000});
  }
  await setChecked("axes", false);
  await setChecked("unit_cell_box", boundary);
  const pending = page.waitForResponse((response) => response.url().includes("/api/v2/camera/action"), { timeout: 20000 });
  await page.locator("#canvas-view-fit").click();
  const action = await (await pending).json();
  const serverEye = action.camera && action.camera.eye;
  const serverCenter = action.camera && action.camera.center;
  const expected = await page.evaluate(async () => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const sceneId = gd.layout.meta.mattervis_render.scene_id;
    const state = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(sceneId)).then((response) => response.json());
    return {
      scale: Number(state.camera_aspect_scale || 1),
      ranges: state.fit_axis_ranges,
      revision: Number(state.camera_revision || 0),
    };
  });
  expected.eyeVec = [Number(serverEye.x), Number(serverEye.y), Number(serverEye.z)];
  expected.eye = Math.hypot(...expected.eyeVec);
  expected.centerVec = [Number(serverCenter.x), Number(serverCenter.y), Number(serverCenter.z)];
  const fittedSpans = ["x", "y", "z"].map((axis) => Math.abs(expected.ranges[axis][1] - expected.ranges[axis][0]));
  const longest = Math.max(...fittedSpans);
  expected.aspect = fittedSpans.map((span) => expected.scale * span / longest);
  expected.spans = fittedSpans;
  const deadline = Date.now() + 20000;
  let last = null;
  let matched = false;
  while (Date.now() < deadline) {
    last = await page.evaluate((target) => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const layout = gd && gd._fullLayout && gd._fullLayout.scene;
      const gl = layout && layout._scene && (layout._scene.camera || (layout._scene.glplot && layout._scene.glplot.camera));
      const aspect = layout && layout.aspectratio;
      const render = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
      const status = document.getElementById("mv-render-status");
      function comp(value, index) {
        if (!value) return null;
        return value.x !== undefined ? Number(value[["x", "y", "z"][index]]) : Number(value[index]);
      }
      const eye = gl && gl.eye ? [0, 1, 2].map((index) => comp(gl.eye, index)) : null;
      const center = gl && gl.center ? [0, 1, 2].map((index) => comp(gl.center, index)) : null;
      const liveSpans = layout && layout.xaxis && layout.xaxis.range ? ["x", "y", "z"].map((axis) => Math.abs(layout[axis + "axis"].range[1] - layout[axis + "axis"].range[0])) : null;
      const aspectLive = aspect ? [Number(aspect.x), Number(aspect.y), Number(aspect.z)] : null;
      let ok = !!(eye && center && liveSpans && aspectLive);
      if (ok) {
        const eyeError = Math.hypot(eye[0] - target.eyeVec[0], eye[1] - target.eyeVec[1], eye[2] - target.eyeVec[2]);
        const centerError = Math.hypot(center[0] - target.centerVec[0], center[1] - target.centerVec[1], center[2] - target.centerVec[2]);
        ok = eyeError / Math.max(target.eye, 1e-6) < 0.02 && centerError / Math.max(target.eye, 1e-6) < 0.02;
        for (let index = 0; index < 3 && ok; index += 1) {
          if (Math.abs(liveSpans[index] - target.spans[index]) / Math.max(target.spans[index], 1e-6) > 0.01) ok = false;
          if (Math.abs(aspectLive[index] - target.aspect[index]) / Math.max(target.aspect[index], 1e-6) > 0.01) ok = false;
        }
        if (render && Number(render.camera_revision) < target.revision) ok = false;
      }
      return {
        ok,
        eye, center, liveSpans, aspectLive,
        revision: render ? Number(render.camera_revision) : null,
        status: status && !status.hidden ? String(status.textContent || "") : "",
        postcondition: (window.__mv_camera_diag && window.__mv_camera_diag.lastPostcondition) || null,
        repairs: window.__mv_camera_diag && window.__mv_camera_diag.repairs || 0,
        lastApply: window.__mv_camera_diag && window.__mv_camera_diag.lastApply || null,
      };
    }, expected);
    if (last.ok) { matched = true; break; }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (!matched) {
    fs.writeFileSync(`${outDir}/matrix-fit-timeout.json`, JSON.stringify({expected, last}, null, 2));
    await page.screenshot({path: `${outDir}/matrix-fit-timeout.png`}).catch(() => {});
    fail("fit layout did not match the response " + JSON.stringify({expected, last}));
  }
  return page.evaluate(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const layout = gd._fullLayout.scene;
    const gl = layout._scene.camera;
    const eye = gl.eye;
    const canvas = gd.querySelector("canvas").getBoundingClientRect();
    const bar = document.getElementById("mv-status-bar");
    const controls = document.getElementById("canvas-view-fit");
    const limits = [canvas.bottom];
    if (bar) limits.push(bar.getBoundingClientRect().top);
    if (controls) limits.push(controls.getBoundingClientRect().top);
    const bottom = Math.min.apply(null, limits);
    function vec(value) {
      const x = value.x !== undefined ? Number(value.x) : Number(value[0]);
      const y = value.y !== undefined ? Number(value.y) : Number(value[1]);
      const z = value.z !== undefined ? Number(value.z) : Number(value[2]);
      return [x, y, z];
    }
    return {
      projection: gl._ortho ? "orthographic" : "perspective",
      eye: vec(eye),
      aspect: {x: layout.aspectratio.x, y: layout.aspectratio.y, z: layout.aspectratio.z},
      ranges: {x: layout.xaxis.range, y: layout.yaxis.range, z: layout.zaxis.range},
      plot: {x: canvas.x, y: canvas.y, width: canvas.width, height: Math.max(1, bottom - canvas.y)},
      chrome: ["compact-toggle-left-panel", "canvas-view-reset", "canvas-view-fit", "mv-status-bar", "mv-render-status", "perf-log-panel"].map((id) => {
        const node = document.getElementById(id);
        if (!node) return null;
        const rect = node.getBoundingClientRect();
        const pad = id === "perf-log-panel" ? 24 : 0;
        return {x: rect.x - pad, y: rect.y - pad, width: rect.width + 2 * pad, height: rect.height + 2 * pad};
      }).filter(Boolean),
    };
  });
}

function readPaint() {
  const gd = document.querySelector("#crystal-graph .js-plotly-plot");
  const layout = gd._fullLayout.scene;
  const gl = layout._scene.camera || (layout._scene.glplot && layout._scene.glplot.camera);
  const eye = gl.eye;
  const center = gl.center;
  const canvas = gd.querySelector("canvas").getBoundingClientRect();
  const bar = document.getElementById("mv-status-bar");
  const controls = document.getElementById("canvas-view-fit");
  const limits = [canvas.bottom];
  if (bar) limits.push(bar.getBoundingClientRect().top);
  if (controls) limits.push(controls.getBoundingClientRect().top);
  const bottom = Math.min.apply(null, limits);
  function vec(value) {
    if (!value) return null;
    const x = value.x !== undefined ? Number(value.x) : Number(value[0]);
    const y = value.y !== undefined ? Number(value.y) : Number(value[1]);
    const z = value.z !== undefined ? Number(value.z) : Number(value[2]);
    return [x, y, z];
  }
  const status = document.getElementById("mv-render-status");
  const render = gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
  const boundaryBox = document.querySelector("#display-options input[value='unit_cell_box']");
  return {
    projection: gl._ortho ? "orthographic" : "perspective",
    eye: vec(eye),
    center: vec(center),
    revision: render ? Number(render.camera_revision) : null,
    renderRevision: render ? Number(render.render_revision) : null,
    sceneId: render ? render.scene_id : null,
    boundaryChecked: !!(boundaryBox && boundaryBox.checked),
    aspect: {x: layout.aspectratio.x, y: layout.aspectratio.y, z: layout.aspectratio.z},
    ranges: {x: layout.xaxis.range, y: layout.yaxis.range, z: layout.zaxis.range},
    status: status && !status.hidden ? String(status.textContent || "") : "",
    postcondition: (window.__mv_camera_diag && window.__mv_camera_diag.lastPostcondition) || null,
    plot: {x: canvas.x, y: canvas.y, width: canvas.width, height: Math.max(1, bottom - canvas.y)},
    canvas: {x: canvas.x, y: canvas.y, width: canvas.width, height: canvas.height},
    chrome: ["compact-toggle-left-panel", "canvas-view-reset", "canvas-view-fit", "mv-status-bar", "mv-render-status", "perf-log-panel"].map((id) => {
      const node = document.getElementById(id);
      if (!node || node.hidden) return null;
      const rect = node.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return null;
      const pad = id === "perf-log-panel" ? 24 : 0;
      return {id, x: rect.x - pad, y: rect.y - pad, width: rect.width + 2 * pad, height: rect.height + 2 * pad};
    }).filter(Boolean),
  };
}

async function capture(page, shots, name, projection, boundary, consoleErrors) {
  const frame = await fit(page, projection, boundary);
  if (frame.projection !== projection) fail(name + " projection " + frame.projection);
  const before = await page.evaluate(readPaint);
  await page.screenshot({path: `${outDir}/${name}.png`});
  const after = await page.evaluate(readPaint);
  const round = (value) => {
    if (Array.isArray(value)) return value.map(round);
    if (value && typeof value === "object") {
      const out = {};
      Object.keys(value).forEach((key) => { out[key] = round(value[key]); });
      return out;
    }
    if (typeof value === "number") return Math.round(value * 1e8) / 1e8;
    return value;
  };
  const tag = (sample) => JSON.stringify({
    revision: sample.revision,
    renderRevision: sample.renderRevision,
    sceneId: sample.sceneId,
    eye: round(sample.eye),
    center: round(sample.center),
    aspect: round(sample.aspect),
    ranges: round(sample.ranges),
    status: sample.status,
    boundaryChecked: sample.boundaryChecked,
    plot: round(sample.plot),
    canvas: round(sample.canvas),
    chrome: round(sample.chrome),
  });
  if (tag(before) !== tag(after)) fail(name + " shutter moved\n" + tag(before) + "\n" + tag(after));
  if (after.projection !== projection) fail(name + " painted projection " + after.projection);
  if (after.boundaryChecked !== boundary) fail(name + " boundary checkbox " + after.boundaryChecked);
  shots.push({file: `${name}.png`, boundary, console: consoleErrors.slice(), before, ...after});
}

async function main() {
  const browser = await chromium.launch({
    executablePath: chrome,
    headless: true,
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--ignore-gpu-blocklist"],
  });
  const page = await browser.newPage({viewport: {width: 1280, height: 800}});
  const shots = [];
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(String(error)));
  try {
    await page.goto(base, {waitUntil: "domcontentloaded"});
    await graphReady(page);
    const catalog = await page.evaluate(async () => {
      const structures = await fetch("/api/v2/structures").then((response) => response.json());
      const scenes = await fetch("/api/v2/scenes").then((response) => response.json());
      return {structures: structures.structures, scenes};
    });
    const names = catalog.structures.map((item) => item.name);
    const nacl = names.find((name) => name.includes("nacl"));
    const water = names.find((name) => name.includes("water"));
    const triclinic = names.find((name) => name.includes("triclinic"));
    if (!nacl || !water || !triclinic) fail("structures " + names.join(","));
    const active = catalog.scenes.scenes.find((scene) => scene.structure === nacl) || catalog.scenes.scenes[0];
    const sceneId = active.id || active.scene_id;
    await showScene(page, sceneId);

    await capture(page, shots, "nacl-off-1280-perspective", "perspective", false, consoleErrors);
    await capture(page, shots, "nacl-off-1280-orthographic", "orthographic", false, consoleErrors);

    const grown = await page.evaluate(async (id) => {
      const state = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(id)).then((response) => response.json());
      const response = await fetch("/api/v2/operate", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          command: "build_supercell",
          structure_id: state.structure,
          scene_id: id,
          expected_revision: 1,
          params: {a: 4, b: 1, c: 1},
        }),
      });
      return response.json();
    }, sceneId);
    const elongated = grown.scene_id;
    if (!elongated) fail("supercell " + JSON.stringify(grown).slice(0, 400));
    await showScene(page, elongated);
    await capture(page, shots, "elongated-1280-perspective", "perspective", true, consoleErrors);
    await capture(page, shots, "elongated-1280-orthographic", "orthographic", true, consoleErrors);
    await page.setViewportSize({width: 1600, height: 700});
    await capture(page, shots, "elongated-1600-orthographic", "orthographic", true, consoleErrors);

    const created = await page.evaluate(async (name) => {
      const response = await fetch("/api/v2/scenes", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({structure: name}),
      });
      return response.json();
    }, triclinic);
    const triclinicId = created.id || created.scene_id;
    if (!triclinicId) fail("triclinic scene " + JSON.stringify(created).slice(0, 300));
    await showScene(page, triclinicId);
    await page.setViewportSize({width: 1280, height: 800});
    await capture(page, shots, "triclinic-1280-perspective", "perspective", true, consoleErrors);
    await page.setViewportSize({width: 700, height: 1000});
    await capture(page, shots, "triclinic-700-orthographic", "orthographic", true, consoleErrors);

    const waterScene = await page.evaluate(async (name) => {
      const response = await fetch("/api/v2/scenes", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({structure: name}),
      });
      return response.json();
    }, water);
    const waterId = waterScene.id || waterScene.scene_id;
    if (!waterId) fail("water scene " + JSON.stringify(waterScene).slice(0, 300));
    await showScene(page, waterId);
    await page.setViewportSize({width: 1280, height: 800});
    await capture(page, shots, "water-off-1280-perspective", "perspective", false, consoleErrors);
    await capture(page, shots, "water-off-1280-orthographic", "orthographic", false, consoleErrors);
    console.log(JSON.stringify(shots));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
