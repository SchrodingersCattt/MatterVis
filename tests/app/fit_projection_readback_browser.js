/* Read the fitted camera back after a figure rebuild, a tab roundtrip, and Reset.
 * Args: playwright module, chrome path, base url, screenshot directory.
 */
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

const layoutSnapshotSource = `function layoutSnapshot() {
  const gd = document.querySelector("#crystal-graph .js-plotly-plot");
  const layout = gd && gd._fullLayout && gd._fullLayout.scene;
  const gl = layout && layout._scene && (layout._scene.camera || (layout._scene.glplot && layout._scene.glplot.camera));
  const render = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
  if (!layout || !gl || !gl.eye || !layout.aspectratio || !layout.xaxis) return null;
  function vec(value) {
    const x = value.x !== undefined ? Number(value.x) : Number(value[0]);
    const y = value.y !== undefined ? Number(value.y) : Number(value[1]);
    const z = value.z !== undefined ? Number(value.z) : Number(value[2]);
    return [x, y, z];
  }
  function span(range) {
    return Math.abs(Number(range[1]) - Number(range[0]));
  }
  return {
    eye: vec(gl.eye),
    aspect: Number(layout.aspectratio.x),
    spans: [span(layout.xaxis.range), span(layout.yaxis.range), span(layout.zaxis.range)],
    revision: render ? Number(render.render_revision) : null,
  };
}`;

async function readLayout(page) {
  return page.evaluate(() => window.__mvFitSnapshot());
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
    const render = (gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render)
      || (gd && gd._fullLayout && gd._fullLayout.meta && gd._fullLayout.meta.mattervis_render);
    const live = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    return !!(render && render.scene_id === id && live && live.glplot && gd.data && gd.data.length);
  }, sceneId, { timeout: 20000 });
}

async function setChecked(page, value, want) {
  const box = page.locator(`#display-options input[value='${value}']`);
  if (!(await box.count())) return;
  if (await box.isDisabled()) return;
  if ((await box.isChecked()) === want) return;
  await box.locator("xpath=ancestor::label[1]").click();
  await page.waitForFunction(({value, want}) => {
    const node = document.querySelector(`#display-options input[value='${value}']`);
    return !node || node.checked === want;
  }, {value, want}, {timeout: 10000});
}

async function fit(page, projection, boundary) {
  await page.evaluate(async (options) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const sceneId = gd.layout.meta.mattervis_render.scene_id;
    await fetch("/api/v2/state", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        scene_id: sceneId,
        display_mode: "unit_cell",
        display_options: options.boundary ? ["unit_cell_box"] : [],
        projection: options.mode,
      }),
    });
  }, {mode: projection, boundary});
  await page.waitForFunction((mode) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const gl = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene.camera;
    return gl && (mode === "orthographic" ? gl._ortho : !gl._ortho);
  }, projection, {timeout: 20000});
  await setChecked(page, "axes", false);
  await setChecked(page, "unit_cell_box", boundary);
  const pending = page.waitForResponse((response) => response.url().includes("/api/v2/camera/action"), {timeout: 20000});
  await page.locator("#canvas-view-fit").click();
  const action = await (await pending).json();
  const serverEye = action.camera && action.camera.eye;
  const expected = {
    eyeVec: [Number(serverEye.x), Number(serverEye.y), Number(serverEye.z)],
    scale: Number(action.camera_aspect_scale || action.scale || 0),
  };
  expected.eye = Math.hypot(...expected.eyeVec);
  const stateScale = await page.evaluate(async () => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const sceneId = gd.layout.meta.mattervis_render.scene_id;
    const state = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(sceneId)).then((response) => response.json());
    return Number(state.camera_aspect_scale || 1);
  });
  expected.scale = stateScale;
  await page.waitForFunction((target) => {
    const shot = window.__mvFitSnapshot();
    if (!shot) return false;
    const got = Math.hypot(shot.eye[0] - target.eyeVec[0], shot.eye[1] - target.eyeVec[1], shot.eye[2] - target.eyeVec[2]);
    if (!(got / Math.max(target.eye, 1e-6) < 0.02)) return false;
    const base = shot.spans[0] / Math.max(...shot.spans);
    return Math.abs(shot.aspect - target.scale * base) / Math.max(target.scale * base, 1e-6) < 0.01;
  }, expected, {timeout: 20000});
  return readLayout(page);
}

async function toggleLabels(page) {
  const before = await page.evaluate(async () => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    if (!gd.__mvAfterPlotBound) {
      gd.__mvAfterPlotBound = true;
      gd.on("plotly_afterplot", () => {
        window.__mvAfterPlot = (window.__mvAfterPlot || 0) + 1;
      });
    }
    const state = await fetch("/api/v2/state").then((response) => response.json());
    return {revision: Number(state.render_revision || 0), plot: window.__mvAfterPlot || 0};
  });
  const box = page.locator("#display-options input[value='labels']");
  const expected = await readLayout(page);
  await setChecked(page, "labels", !(await box.isChecked()));
  let matched = null;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    matched = await page.evaluate(async (previous) => {
      const state = await fetch("/api/v2/state").then((response) => response.json());
      const shot = window.__mvFitSnapshot();
      if (!shot || !(Number(state.render_revision || 0) > previous.revision)) return null;
      const ok = previous.spans.every((span, index) => Math.abs(shot.spans[index] - span) / Math.max(Math.abs(span), 1e-6) < 0.01);
      return ok ? shot : null;
    }, {revision: before.revision, spans: expected.spans});
    if (matched) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!matched) fail("fitted frame did not survive the figure rebuild");
  return readLayout(page);
}

async function main() {
  const browser = await chromium.launch({
    executablePath: chrome,
    headless: true,
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--ignore-gpu-blocklist"],
  });
  const page = await browser.newPage({viewport: {width: 1280, height: 800}});
  try {
    await page.addInitScript(layoutSnapshotSource + "\nwindow.__mvFitSnapshot = layoutSnapshot;");
    await page.goto(base, {waitUntil: "domcontentloaded"});
    await graphReady(page);
    const sceneId = await page.evaluate(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      return gd.layout.meta.mattervis_render.scene_id;
    });
    const fitted = await fit(page, "orthographic", true);
    const boundaryOn = await page.evaluate(() => {
      const box = document.querySelector("#display-options input[value='unit_cell_box']");
      return !!(box && box.checked);
    });
    const reacted = await toggleLabels(page);
    const structure = await page.evaluate(async (id) => {
      const state = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(id)).then((response) => response.json());
      return state.structure;
    }, sceneId);
    const created = await page.evaluate(async (name) => {
      const response = await fetch("/api/v2/scenes", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({structure: name}),
      });
      return response.json();
    }, structure);
    const otherId = created.id || created.scene_id;
    if (!otherId) fail("second scene " + JSON.stringify(created).slice(0, 300));
    await showScene(page, otherId);
    await showScene(page, sceneId);
    await page.waitForFunction((expected) => {
      const shot = window.__mvFitSnapshot();
      if (!shot) return false;
      return expected.every((span, index) => Math.abs(shot.spans[index] - span) / Math.max(Math.abs(span), 1e-6) < 0.01);
    }, fitted.spans, {timeout: 20000});
    const returned = await readLayout(page);
    const pendingReset = page.waitForResponse((response) => response.url().includes("/api/v2/camera/action"), {timeout: 20000});
    await page.locator("#canvas-view-reset").click();
    await pendingReset;
    await page.waitForFunction((fittedEye) => {
      return fetch("/api/v2/state").then((response) => response.json()).then((state) => {
        const shot = window.__mvFitSnapshot();
        if (!(Number(state.camera_aspect_scale || 1) === 1 && !state.fit_axis_ranges && shot)) return false;
        const delta = Math.hypot(shot.eye[0] - fittedEye[0], shot.eye[1] - fittedEye[1], shot.eye[2] - fittedEye[2]);
        const length = Math.hypot(fittedEye[0], fittedEye[1], fittedEye[2]);
        return delta / Math.max(length, 1e-6) > 0.02 && Math.abs(shot.aspect - 1) < 0.01 && shot.spans[0] > 1;
      });
    }, fitted.eye, {timeout: 20000});
    const reset = await readLayout(page);
    const beforeShot = await page.evaluate(() => window.__mvFitSnapshot());
    await page.screenshot({path: `${outDir}/reset.png`});
    const afterShot = await page.evaluate(() => window.__mvFitSnapshot());
    if (JSON.stringify(beforeShot) !== JSON.stringify(afterShot)) {
      fail("reset shutter moved " + JSON.stringify({beforeShot, afterShot}));
    }
    const plot = await page.evaluate(() => {
      const canvas = document.querySelector("#crystal-graph canvas");
      const rect = canvas.getBoundingClientRect();
      const bar = document.getElementById("mv-status-bar");
      const controls = document.getElementById("canvas-view-fit");
      const limits = [rect.bottom];
      if (bar) limits.push(bar.getBoundingClientRect().top);
      if (controls) limits.push(controls.getBoundingClientRect().top);
      const bottom = Math.min.apply(null, limits);
      const extra = {"perf-log-panel": 24};
      return {
        plot: {x: rect.x, y: rect.y, width: rect.width, height: Math.max(1, bottom - rect.y)},
        chrome: ["compact-toggle-left-panel", "canvas-view-reset", "canvas-view-fit", "mv-status-bar", "mv-render-status", "perf-log-panel"].map((id) => {
          const node = document.getElementById(id);
          if (!node) return null;
          const rect = node.getBoundingClientRect();
          const pad = extra[id] || 0;
          return {x: rect.x - pad, y: rect.y - pad, width: rect.width + 2 * pad, height: rect.height + 2 * pad};
        }).filter(Boolean),
      };
    });
    await setChecked(page, "labels", false);
    await setChecked(page, "unit_cell_box", false);
    const cluster = await fit(page, "orthographic", false);
    const boundaryOff = await page.evaluate(() => {
      const box = document.querySelector("#display-options input[value='unit_cell_box']");
      return !!(box && box.checked);
    });
    const clusterReact = await toggleLabels(page);
    const status = await page.evaluate(() => {
      const node = document.getElementById("mv-render-status");
      return {
        status: node && !node.hidden ? String(node.textContent || "") : "",
        postcondition: (window.__mv_camera_diag && window.__mv_camera_diag.lastPostcondition) || null,
      };
    });
    console.log(JSON.stringify({
      fitted, reacted, returned, reset, cluster, clusterReact,
      file: "reset.png",
      plot: plot.plot,
      chrome: plot.chrome,
      status: status.status,
      postcondition: status.postcondition,
      boundaryOn,
      boundaryOff,
    }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
