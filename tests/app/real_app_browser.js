/* Drive the real MatterVis page through the hand-reproduced bugs.
 * Args: playwright module, chrome path, base url, mode, optional upload path,
 * optional structure, optional restart-ready file.
 */
const fs = require("node:fs");
const zlib = require("node:zlib");
const { chromium } = require(process.argv[2]);
const chrome = process.argv[3];
const base = process.argv[4];
const mode = process.argv[5];
const uploadPath = process.argv[6] || "";
const structure = process.argv[7] || "";
const readyPath = process.argv[8] || "";

function fail(message) {
  throw new Error(message);
}
function log(message) {
  console.error(new Date().toISOString() + " " + message);
}

async function graphReady(page) {
  await page.waitForFunction(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    return !!(scene && scene.glplot && scene.camera && gd.data && gd.data.length);
  }, { timeout: 90000 });
}

async function readView(page) {
  return page.evaluate(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const scene = gd._fullLayout.scene._scene;
    const eye = scene.camera && scene.camera.eye;
    const center = scene.camera && scene.camera.center;
    const x = eye ? Number(eye[0] !== undefined ? eye[0] : eye.x) : NaN;
    const y = eye ? Number(eye[1] !== undefined ? eye[1] : eye.y) : NaN;
    const z = eye ? Number(eye[2] !== undefined ? eye[2] : eye.z) : NaN;
    const dist = Math.hypot(x, y, z);
    const layoutEye = gd.layout && gd.layout.scene && gd.layout.scene.camera && gd.layout.scene.camera.eye;
    const aspect = scene.glplot.getAspectratio ? scene.glplot.getAspectratio() : null;
    const title = gd.layout && gd.layout.title;
    const text = title && (title.text || title) || "";
    const meta = gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    const traces = Array.isArray(gd.data) ? gd.data.length : 0;
    const radio = document.querySelector("#view-projection input:checked");
    return {
      dist,
      eye: eye ? {0: eye[0], 1: eye[1], 2: eye[2], x: eye.x, y: eye.y, z: eye.z} : null,
      center: center ? {x: Number(center[0] !== undefined ? center[0] : center.x), y: Number(center[1] !== undefined ? center[1] : center.y), z: Number(center[2] !== undefined ? center[2] : center.z)} : null,
      layoutEye,
      aspect,
      ortho: !!scene.camera._ortho,
      title: String(text),
      sceneId: meta && meta.scene_id ? String(meta.scene_id) : "",
      traces,
      radio: radio ? radio.value : "",
    };
  });
}

function eyeComponent(eye, index, name) {
  if (!eye) return NaN;
  if (eye[index] !== undefined && eye[index] !== null) return Number(eye[index]);
  if (eye[name] !== undefined && eye[name] !== null) return Number(eye[name]);
  return NaN;
}

function visibleTab(page, name) {
  // The current workbench keeps the dcc.Tabs strip visible. Each tab owns
  // its close button and carries the scene label in .scene-tab-text.
  return page.locator("#scene-tabs .tab").filter({ hasText: name }).first();
}

async function selectedTabLabel(page) {
  return page.evaluate(() => {
    const chip = document.querySelector(".scene-tab-chip--selected .tab-select");
    if (chip) return String(chip.textContent || "").replace(/\s+/g, " ").trim();
    const root = document.getElementById("scene-tabs");
    if (!root) return "";
    const selected = root.querySelector(".tab--selected, [aria-selected='true'], .dash-tab--selected, .rc-tabs-tab-active");
    return selected ? String(selected.textContent || "").replace(/\s+/g, " ").trim() : "";
  });
}

async function clickTab(page, name) {
  const tabs = page.locator("#scene-tabs");
  const chip = visibleTab(page, name);
  const hidden = tabs.getByText(name, { exact: false }).first();
  try {
    await chip.click({ timeout: 15000 });
  } catch (chipError) {
    try {
      await hidden.click({ timeout: 5000, force: true });
    } catch (error) {
      const labels = await tabs.innerText().catch(() => "");
      const chips = await page.locator("#scene-tab-close-row").innerText().catch(() => "");
      throw new Error(`could not click tab ${name}: ${chipError.message}\n tabs: ${labels}\n chips: ${chips}`);
    }
  }
  try {
    await page.waitForFunction((wanted) => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const title = gd && gd.layout && gd.layout.title;
      const text = String((title && (title.text || title)) || "");
      const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
      const scene = meta && String(meta.scene_id || "");
      const structure = gd && gd.layout && gd.layout.meta && (gd.layout.meta.structure || "");
      const blob = (text + " " + scene + " " + structure).toLowerCase();
      return blob.includes(String(wanted).toLowerCase());
    }, name, { timeout: 60000 });
  } catch (error) {
    const view = await readView(page).catch(() => ({}));
    const labels = await tabs.innerText().catch(() => "");
    const selected = await selectedTabLabel(page).catch(() => "");
    const diag = await page.evaluate(async () => {
      const updates = await fetch("/api/v2/view-updates?since=0", {cache: "no-store"})
        .then((response) => response.json())
        .catch((error) => ({error: String(error)}));
      const rows = (updates && updates.updates) || [];
      return {
        diag: window.__mv_view_diag || {},
        tail: rows.slice(-6).map((item) => ({
          type: item.type,
          scene_id: item.scene_id,
          kind: item.update_kind || item.kind,
          reason: item.reason,
          revision: item.render_revision,
          camera: item.camera_version || item.camera_revision,
          hasFigure: !!(item.figure && item.figure.data),
        })),
      };
    }).catch((error) => String(error));
    const server = await page.evaluate(async () => {
      const scenes = await fetch("/api/v2/scenes", {cache: "no-store"}).then((response) => response.json());
      const state = await fetch("/api/v2/state", {cache: "no-store"}).then((response) => response.json());
      return {
        active: scenes && scenes.active_id,
        structures: (scenes.scenes || []).map((scene) => scene.structure_name + ":" + scene.id),
        structure: state && state.structure,
        scene_id: state && state.scene_id,
        revision: state && state.render_revision,
        camera: state && state.camera_revision,
      };
    }).catch((error) => String(error));
    throw new Error(`tab ${name} did not paint. selected=${selected} server=${JSON.stringify(server)} view=${JSON.stringify(view)} diag=${JSON.stringify(diag)} tabs=${labels}`);
  }
}

async function wheel(page, deltaY, times) {
  const canvas = page.locator("#crystal-graph canvas").first();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("crystal canvas has no box");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < times; i += 1) {
    await page.mouse.wheel(0, deltaY);
    await page.waitForTimeout(40);
  }
  await page.waitForTimeout(200);
}

async function runReset(page) {
  if (structure) await clickTab(page, structure);
  await graphReady(page);
  const start = await readView(page);
  await page.locator("#view-projection input[value='orthographic']").click();
  try {
  await page.waitForFunction(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const camera = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene && gd._fullLayout.scene._scene.camera;
    if (!camera || !camera._ortho || !camera.eye) return false;
    const eye = camera.eye;
    const dist = Math.hypot(Number(eye[0] !== undefined ? eye[0] : eye.x), Number(eye[1] !== undefined ? eye[1] : eye.y), Number(eye[2] !== undefined ? eye[2] : eye.z));
    return Number.isFinite(dist) && dist > 0.05;
  }, { timeout: 20000 });
  } catch (error) {
    fail(`orthographic camera was not numeric: ${JSON.stringify(await readView(page))} (${error.message})`);
  }
  const ortho = await readView(page);
  await wheel(page, -280, 10);
  const zoomed = await readView(page);
  if (!(zoomed.dist < ortho.dist * 0.75)) {
    fail(`orthographic wheel did not zoom the eye in (${ortho.dist} -> ${zoomed.dist})`);
  }
  if (ortho.aspect && zoomed.aspect) {
    ["x", "y", "z"].forEach((axis) => {
      const base = Number(ortho.aspect[axis]);
      const got = Number(zoomed.aspect[axis]);
      if (Math.abs(got - base) / Math.max(Math.abs(base), 1e-6) > 0.08) {
        fail(`orthographic wheel changed aspect ${axis}: ${base} -> ${got}`);
      }
    });
  }
  await page.locator("#view-projection input[value='perspective']").click();
  await page.waitForFunction(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    return !!(gd && gd._fullLayout.scene._scene.camera && !gd._fullLayout.scene._scene.camera._ortho);
  }, { timeout: 20000 });
  await page.locator("#view-reset").click();
  await page.waitForTimeout(400);
  await page.locator("#view-reset").click();
  await page.waitForFunction((expected) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const eye = gd._fullLayout.scene._scene.camera.eye;
    const dist = Math.hypot(Number(eye[0] || eye.x), Number(eye[1] || eye.y), Number(eye[2] || eye.z));
    return Math.abs(dist - expected) / Math.max(expected, 1e-6) < 0.35;
  }, start.dist, { timeout: 20000 }).catch(() => {});
  const reset = await readView(page);
  if (Math.abs(reset.dist - start.dist) / Math.max(start.dist, 1e-6) > 0.35) {
    fail(`reset left the camera zoomed (${start.dist} -> ${reset.dist})`);
  }
  if (reset.ortho) fail("reset left the camera orthographic after a perspective switch");
  await wheel(page, 240, 3);
  const zoomedOut = await readView(page);
  if (zoomedOut.dist > 8) {
    fail(`three perspective wheel notches exploded the eye to ${zoomedOut.dist}`);
  }
  if (zoomedOut.dist > start.dist * 4) {
    fail(`three perspective wheel notches shrank the model too far (${start.dist} -> ${zoomedOut.dist})`);
  }
}

async function nonblank(page) {
  const view = await readView(page);
  if (!(view.traces > 0) || !Number.isFinite(view.dist) || view.dist < 0.05) {
    fail(`view is blank ${JSON.stringify(view)}`);
  }
  return view;
}

async function postAction(page, body) {
  const result = await page.evaluate(async (payload) => {
    const response = await fetch("/api/v2/camera/action", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    let body = null;
    try { body = await response.json(); } catch (_) {}
    return {status: response.status, body};
  }, body);
  if (result.status !== 200) fail(`camera action failed: ${result.status} ${JSON.stringify(body)}`);
  return result.body;
}

async function runGestures(page) {
  if (structure) await clickTab(page, structure);
  await graphReady(page);
  await page.locator("#view-projection input[value='orthographic']").click();
  await page.waitForTimeout(300);
  const ortho = await nonblank(page);
  if (!ortho.ortho) fail("orthographic radio did not reach the GL camera");
  const fitResult = await postAction(page, {action: "fit"});
  const fitEye = fitResult && fitResult.camera && fitResult.camera.eye;
  await page.waitForFunction((expected) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const eye = gd && gd.layout && gd.layout.scene && gd.layout.scene.camera && gd.layout.scene.camera.eye;
    if (!eye || !expected) return false;
    const component = (value, index, key) => Number(value && value[key] !== undefined ? value[key] : value && value[index]);
    const got = [component(eye, 0, "x"), component(eye, 1, "y"), component(eye, 2, "z")];
    const want = [component(expected, 0, "x"), component(expected, 1, "y"), component(expected, 2, "z")];
    if (![...got, ...want].every(Number.isFinite)) return false;
    const gotNorm = Math.hypot(...got), wantNorm = Math.hypot(...want);
    if (!(gotNorm > 0.05 && wantNorm > 0.05)) return false;
    const dot = got.reduce((sum, value, index) => sum + value * want[index], 0) / (gotNorm * wantNorm);
    return dot >= 0.95;
  }, fitEye, {timeout: 20000});
  await page.locator("#view-align-a").click();
  await page.waitForTimeout(400);
  const aligned = await nonblank(page);
  const alignedEye = [
    eyeComponent(aligned.eye, 0, "x"),
    eyeComponent(aligned.eye, 1, "y"),
    eyeComponent(aligned.eye, 2, "z"),
  ];
  const orbitResult = await postAction(page, {action: "orbit", yaw_deg: 25, pitch_deg: 10});
  try {
    await page.waitForFunction((previous) => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const eye = gd && gd._fullLayout.scene._scene.camera.eye;
      if (!eye) return false;
      const x = Number(eye[0] !== undefined ? eye[0] : eye.x);
      const y = Number(eye[1] !== undefined ? eye[1] : eye.y);
      const z = Number(eye[2] !== undefined ? eye[2] : eye.z);
      return [x, y, z].every(Number.isFinite) && Math.hypot(x - previous[0], y - previous[1], z - previous[2]) > 0.05;
    }, alignedEye, {timeout: 20000});
  } catch (error) {
    const now = await readView(page);
    const diagnostics = await page.evaluate((orbitResult) => ({
      view: window.__mv_view_diag || null,
      camera: window.__mv_camera_diag || null,
      render: window.__mvRenderStatus || null,
      metadata: document.getElementById("fast-view-metadata")?.textContent || "",
      orbitResult,
    }), orbitResult);
    fail(`orbit left the eye unchanged (${JSON.stringify(aligned.eye)} -> ${JSON.stringify(now.eye)}), diagnostics=${JSON.stringify(diagnostics)}`);
  }
  const beforePan = await readView(page);
  await postAction(page, {action: "pan", dx: 0.35, dy: -0.2, dz: 0.1});
  await page.waitForFunction((previous) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const center = gd && gd._fullLayout.scene._scene.camera.center;
    if (!center) return false;
    const x = Number(center[0] !== undefined ? center[0] : center.x);
    return Number.isFinite(x) && Math.abs(x - previous) > 0.05;
  }, beforePan.center ? beforePan.center.x : 0, {timeout: 20000});
  const canvas = page.locator("#crystal-graph canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 90, box.y + box.height / 2 + 40, {steps: 6});
  await page.mouse.up();
  await page.locator("#view-reset").click();
  await page.waitForTimeout(500);
  const reset = await nonblank(page);
  if (reset.radio && reset.radio !== "orthographic") fail(`reset changed the radio to ${reset.radio}`);
  if (!reset.ortho) fail("reset dropped orthographic projection");
}

async function runHeld(page) {
  if (structure) await clickTab(page, structure);
  await graphReady(page);
  await page.evaluate(() => {
    const original = window.Plotly.relayout.bind(window.Plotly);
    window.__heldRelease = [];
    window.__holdDragRelayout = true;
    window.Plotly.relayout = function (gd, update) {
      const text = JSON.stringify(update || {});
      const drag = window.__holdDragRelayout && text.indexOf("scene.camera") >= 0 && text.indexOf("#reset") < 0;
      const result = original(gd, update);
      if (!drag) return result;
      return new Promise((resolve) => {
        window.__heldRelease.push(() => Promise.resolve(result).then(resolve, resolve));
      });
    };
  });
  const canvas = page.locator("#crystal-graph canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 + 30, {steps: 8});
  await page.mouse.up();
  await page.waitForTimeout(250);
  await page.evaluate(() => { window.__holdDragRelayout = false; });
  await page.locator("#view-reset").click();
  await page.waitForTimeout(600);
  const reset = await nonblank(page);
  await page.evaluate(() => { (window.__heldRelease || []).splice(0).forEach((release) => release()); });
  await page.waitForTimeout(500);
  const after = await nonblank(page);
  if (Math.abs(after.dist - reset.dist) / Math.max(reset.dist, 1e-6) > 0.45) {
    fail(`held drag overwrote reset (${reset.dist} -> ${after.dist})`);
  }
}

async function clickTabControl(page, name) {
  const chip = visibleTab(page, name);
  try {
    await chip.click({ timeout: 15000 });
  } catch (chipError) {
    const hidden = page.locator("#scene-tabs").getByText(name, { exact: false }).first();
    await hidden.click({ timeout: 5000, force: true });
  }
  await page.waitForFunction((wanted) => {
    const wantedText = String(wanted).toLowerCase();
    const chip = document.querySelector(".scene-tab-chip--selected .tab-select");
    if (String((chip && chip.textContent) || "").toLowerCase().includes(wantedText)) return true;
    const root = document.getElementById("scene-tabs");
    const selected = root && root.querySelector(".tab--selected, [aria-selected='true'], .dash-tab--selected");
    return String((selected && selected.textContent) || "").toLowerCase().includes(wantedText);
  }, name, { timeout: 20000 });
}

async function runRapid(page) {
  await graphReady(page);
  const canvas = page.locator("#crystal-graph canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await clickTabControl(page, "molecular_water");
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 20, {steps: 4});
  await clickTabControl(page, "ionic_nacl");
  await page.mouse.up();
  await clickTab(page, "ionic_nacl");
  const view = await nonblank(page);
  const selected = await selectedTabLabel(page);
  if (!/ionic_nacl/i.test(selected) || (!/ionic_nacl/i.test(view.title) && !/ionic_nacl/i.test(view.sceneId))) {
    fail(`rapid switch left ${selected} / ${view.title} / ${view.sceneId}`);
  }
}

async function runSessions(page, browser) {
  const secondProblems = [];
  const second = await browser.newPage({viewport: {width: 1200, height: 800}});
  second.on("pageerror", (error) => {
    const text = String(error && error.message || error);
    if (/c\.on is not a function|Invalid frame header/i.test(text)) secondProblems.push(text);
  });
  await second.goto(base, {waitUntil: "domcontentloaded", timeout: 30000});
  await graphReady(page);
  await graphReady(second);
  await clickTab(page, "ionic_nacl");
  await clickTab(second, "molecular_water");
  if (uploadPath) {
    await page.setInputFiles("#scene-cif-upload-input", uploadPath);
    await page.waitForTimeout(1500);
  }
  await second.waitForTimeout(500);
  const other = await selectedTabLabel(second);
  const otherView = await readView(second);
  if (!/water/i.test(other) && !/water/i.test(otherView.title) && !/water/i.test(otherView.sceneId)) {
    fail(`upload on the first page stole the second page selection: ${other} / ${otherView.title} / ${otherView.sceneId}`);
  }
  await page.locator("#view-reset").click();
  await second.locator("#view-projection input[value='orthographic']").click();
  await page.waitForTimeout(400);
  const first = await nonblank(page);
  const secondView = await nonblank(second);
  if (first.sceneId && secondView.sceneId && first.sceneId === secondView.sceneId) {
    fail("two browsers collapsed onto one scene");
  }
  if (secondProblems.length) fail(secondProblems.join("\n"));
  await second.close();
}

async function runLeak(page) {
  await graphReady(page);
  for (let i = 0; i < 100; i += 1) {
    await clickTab(page, i % 2 === 0 ? "ionic_nacl" : "molecular_water");
  }
  const census = await page.evaluate(() => {
    const root = document.getElementById("crystal-graph");
    return {
      census: window.__mvCensus || {},
      canvases: root ? root.querySelectorAll("canvas").length : 0,
    };
  });
  if (Number(census.census.socketsOpen || 0) !== 1) fail(`socket census ${JSON.stringify(census)}`);
  if (Number(census.census.webglContexts || 0) > 2) fail(`webgl contexts leaked ${JSON.stringify(census)}`);
  if (census.canvases > 3) fail(`retired canvases stayed in the graph ${JSON.stringify(census)}`);
  await nonblank(page);
  const compass = await page.evaluate(() => {
    const diag = window.__mv_compass_diag || {};
    return {attached: diag.maybe_attach_attached || 0, binds: (window.__mvCensus || {}).compassBinds || 0};
  });
  if (!(compass.attached >= 1 || compass.binds >= 1)) fail(`compass did not stay attached ${JSON.stringify(compass)}`);
}

async function runRestart(page) {
  await graphReady(page);
  await clickTab(page, "DAP-4");
  const before = await readView(page);
  if (!before.sceneId) fail("DAP-4 had no scene id before restart");
  if (uploadPath) {
    await page.setInputFiles("#scene-cif-upload-input", uploadPath);
    await page.waitForFunction(() => {
      const tabs = document.getElementById("scene-tabs");
      return tabs && /porous_framework/i.test(tabs.textContent || "");
    }, { timeout: 90000 });
  }
  const health = await page.evaluate(async (url) => {
    const response = await fetch(url, { cache: "no-store" });
    return response.json();
  }, base + "/api/v2/healthz");
  if (!readyPath) fail("restart mode needs a ready-file path");
  fs.writeFileSync(readyPath, JSON.stringify({
    sceneId: before.sceneId,
    started: health.server_started_at,
  }));
  const deadline = Date.now() + 240000;
  let restarted = false;
  while (Date.now() < deadline) {
    try {
      const body = await page.evaluate(async (url) => {
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      }, base + "/api/v2/healthz");
      if (body.server_started_at && body.server_started_at !== health.server_started_at) {
        restarted = true;
        break;
      }
    } catch (_error) {
      /* The first process is down until the restarted server binds. */
    }
    await page.waitForTimeout(300);
  }
  if (!restarted) fail("server did not restart");
  await page.reload({ waitUntil: "domcontentloaded", timeout: 30000 });
  await graphReady(page);
  const build = await page.evaluate(() => window.MATTERVIS_BUILD || null);
  if (!build || !build.process || build.process !== build.assets) {
    fail("build identity mismatch after reload " + JSON.stringify(build));
  }
  if (await page.locator("#mv-build-mismatch").count()) {
    fail("refresh banner shown for a matching build");
  }
  const tabs = await page.locator("#scene-tabs").innerText();
  if (!/porous_framework/i.test(tabs)) fail("uploaded scene was not restored: " + tabs);
  await clickTab(page, "DAP-4");
  const dap = await readView(page);
  await clickTab(page, "showcase_dap7");
  const show = await readView(page);
  if (!dap.sceneId || !show.sceneId || dap.sceneId === show.sceneId) {
    fail("canvas scene id did not change after restart " + JSON.stringify({ dap, show }));
  }
}

function centerDelta(a, b) {
  if (!a || !b) return Infinity;
  const ax = Number(a.x), ay = Number(a.y), az = Number(a.z);
  const bx = Number(b.x), by = Number(b.y), bz = Number(b.z);
  if (![ax, ay, az, bx, by, bz].every(Number.isFinite)) return Infinity;
  return Math.hypot(ax - bx, ay - by, az - bz);
}

async function serverCamera(page, sceneId) {
  return page.evaluate(async (id) => {
    const response = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(id), {cache: "no-store"});
    const body = await response.json();
    return body && body.camera;
  }, sceneId);
}

async function assertNaclFraming(page, baseline) {
  const view = await readView(page);
  if (view.sceneId !== baseline.sceneId) {
    fail(`ionic_nacl scene changed ${baseline.sceneId} -> ${view.sceneId}`);
  }
  if (!(view.traces > 2) || !Number.isFinite(view.dist) || view.dist < 0.35 || view.dist > 8) {
    fail(`ionic_nacl camera left the lattice ${JSON.stringify(view)}`);
  }
  if (Math.abs(view.dist - baseline.dist) / Math.max(baseline.dist, 1e-6) > 0.55) {
    fail(`ionic_nacl zoom leaked (${baseline.dist} -> ${view.dist})`);
  }
  if (centerDelta(view.center, baseline.center) > 0.45) {
    fail(`ionic_nacl center leaked ${JSON.stringify({baseline: baseline.center, view: view.center})}`);
  }
  const stored = await serverCamera(page, baseline.sceneId);
  if (JSON.stringify(stored) !== JSON.stringify(baseline.stored)) {
    fail(`stored ionic_nacl camera changed ${JSON.stringify(baseline.stored)} -> ${JSON.stringify(stored)}`);
  }
}

function viewMoved(before, after) {
  if (!before || !after) return false;
  if (centerDelta(before.center, after.center) > 0.08) return true;
  if (!Number.isFinite(before.dist) || !Number.isFinite(after.dist)) return false;
  return Math.abs(after.dist - before.dist) / Math.max(before.dist, 1e-6) > 0.08;
}

async function rightDrag(page, box) {
  const x = box.x + box.width * 0.55;
  await page.mouse.move(x, box.y + box.height * 0.62);
  await page.mouse.down({button: "right"});
  await page.mouse.move(x, box.y + box.height * 0.12, {steps: 18});
}

async function runNaclFrame(page) {
  await graphReady(page);
  await clickTab(page, "ionic_nacl");
  const baseline = await readView(page);
  baseline.stored = await serverCamera(page, baseline.sceneId);
  if (!(baseline.traces > 2) || !(baseline.dist > 0.4 && baseline.dist < 8)) {
    fail(`ionic_nacl baseline is not framed ${JSON.stringify(baseline)}`);
  }
  await clickTab(page, "DAP-4");
  const dapView = await readView(page);
  const canvas = page.locator("#crystal-graph canvas").first();
  const box = await canvas.boundingBox();
  if (!box) fail("crystal canvas has no box");
  // A finished drag must move DAP-4. Otherwise the race below never
  // produces the camera update that used to land on ionic_nacl.
  await rightDrag(page, box);
  await page.mouse.up({button: "right"});
  try {
    await page.waitForFunction((previous) => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const camera = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene && gd._fullLayout.scene._scene.camera;
      if (!camera || !camera.eye) return false;
      const eye = camera.eye;
      const center = camera.center || {x: 0, y: 0, z: 0};
      const dist = Math.hypot(Number(eye[0] !== undefined ? eye[0] : eye.x), Number(eye[1] !== undefined ? eye[1] : eye.y), Number(eye[2] !== undefined ? eye[2] : eye.z));
      const cx = Number(center[0] !== undefined ? center[0] : center.x);
      const cy = Number(center[1] !== undefined ? center[1] : center.y);
      const cz = Number(center[2] !== undefined ? center[2] : center.z);
      const pc = previous.center || {x: 0, y: 0, z: 0};
      const centerShift = Math.hypot(cx - Number(pc.x || 0), cy - Number(pc.y || 0), cz - Number(pc.z || 0));
      const zoom = Math.abs(dist - Number(previous.dist)) / Math.max(Number(previous.dist), 1e-6);
      return centerShift > 0.08 || zoom > 0.08;
    }, {dist: dapView.dist, center: dapView.center}, {timeout: 8000});
  } catch (error) {
    fail(`right-drag did not move the DAP-4 camera ${JSON.stringify({before: dapView, after: await readView(page)})}`);
  }
  const dragged = await readView(page);
  if (dragged.sceneId !== dapView.sceneId || !viewMoved(dapView, dragged)) {
    fail(`DAP-4 drag was not committed ${JSON.stringify({before: dapView, after: dragged})}`);
  }
  await rightDrag(page, box);
  // The tab value changes while the drag is still down, which is the
  // race that used to store DAP-4's camera on ionic_nacl.
  await page.evaluate(() => {
    const node = Array.from(document.querySelectorAll("[id^='scene-tab-']"))
      .find((el) => el.id.indexOf("scene-tab-close-") !== 0 && /ionic_nacl/i.test(el.textContent || ""));
    if (!node || !window.dash_clientside || typeof window.dash_clientside.set_props !== "function") {
      throw new Error("cannot retarget the tab during the drag");
    }
    window.dash_clientside.set_props("scene-tabs", {value: node.id.slice("scene-tab-".length)});
  });
  await page.mouse.up({button: "right"});
  await clickTab(page, "ionic_nacl");
  await assertNaclFraming(page, baseline);
  await clickTab(page, "showcase_dap7");
  await clickTab(page, "ionic_nacl");
  await assertNaclFraming(page, baseline);
}

async function scanCanvas(page, callback, options) {
  const canvas = page.locator("#crystal-graph canvas").first();
  const box = await canvas.boundingBox();
  if (!box) fail("crystal canvas has no box");
  const columns = (options && options.columns) || 14;
  const rows = (options && options.rows) || 10;
  const pause = (options && options.pause) || 115;
  for (let row = 1; row < rows; row += 1) {
    const y = box.y + (box.height * row) / rows;
    for (let column = 1; column < columns; column += 1) {
      const x = box.x + (box.width * column) / columns;
      await page.mouse.move(x, y);
      await page.waitForTimeout(pause);
      const result = await callback(x, y);
      if (result) return {x, y, result};
    }
  }
  return null;
}

async function runSemanticPick(page) {
  await clickTab(page, structure || "ionic_nacl");
  await graphReady(page);

  // SwiftShader does not consistently emit Plotly's gl3d click event for an
  // atom. Find a point through the coordinate hover endpoint and click it as
  // a real browser gesture.
  const atomHit = await scanCanvas(page, async () => {
    return page.evaluate(() => {
      const tip = document.getElementById("mv-atom-tip");
      const text = String((tip && tip.textContent) || "").trim();
      return tip && !tip.hidden && text ? text : null;
    });
  });
  if (!atomHit) fail("could not hover an atom long enough to show #mv-atom-tip");
  const atomLabel = String(atomHit.result).split(" (")[0].trim();
  await page.mouse.click(atomHit.x, atomHit.y);
  await page.waitForFunction(async (label) => {
    const response = await fetch("/api/v2/selection", {cache: "no-store"});
    if (!response.ok) return false;
    const body = await response.json();
    const labels = body && body.selection && body.selection.atom_labels;
    return Array.isArray(labels) && labels.includes(label);
  }, atomLabel, {timeout: 20000});

  // Ionic NaCl has default coordination overlays (polyhedron markers). The
  // scan also accepts a BFDH facet when that is the non-atom target painted by
  // a renderer/version. Read Plotly's hover cache after each real move so
  // this remains a native click rather than a synthetic event.
  const nonAtomHit = await scanCanvas(page, async () => {
    return page.evaluate(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const points = gd && gd._hoverdata && gd._hoverdata.points;
      if (!Array.isArray(points)) return null;
      const point = points.find((item) => {
        const cd = item && item.customdata;
        return Array.isArray(cd) && (cd[0] === "polyhedron" || cd[0] === "facet");
      });
      if (!point) return null;
      const cd = point.customdata;
      return {kind: String(cd[0]), id: String(cd[4] || cd[1] || "")};
    });
  }, {columns: 18, rows: 12, pause: 80});
  if (!nonAtomHit) fail("could not hover a polyhedron or facet for the additive semantic click");
  await page.keyboard.down("Shift");
  await page.mouse.click(nonAtomHit.x, nonAtomHit.y);
  await page.keyboard.up("Shift");
  await page.waitForFunction(async ({kind, id, label}) => {
    const response = await fetch("/api/v2/selection", {cache: "no-store"});
    if (!response.ok) return false;
    const body = await response.json();
    const selection = body && body.selection;
    const labels = selection && selection.atom_labels;
    const targets = selection && selection.targets;
    return Array.isArray(labels) && labels.includes(label)
      && Array.isArray(targets)
      && targets.some((target) => target && target.kind === kind && (!id || String(target.id) === id));
  }, {kind: nonAtomHit.result.kind, id: nonAtomHit.result.id, label: atomLabel}, {timeout: 20000});
}

// A blank orthographic canvas is the page background. DAP-4's unit cell
// paints well above this; the axes triad is an SVG overlay, not canvas ink.
const CANVAS_INK_FLOOR = 0.02;

function pngInk(png) {
  let offset = 8;
  let width = 0;
  let height = 0;
  let colorType = 2;
  const idats = [];
  while (offset + 8 <= png.length) {
    const length = png.readUInt32BE(offset);
    const type = png.toString("ascii", offset + 4, offset + 8);
    const data = png.subarray(offset + 8, offset + 8 + length);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      colorType = data[9];
      if (data[12] !== 0) throw new Error("interlaced canvas screenshot");
    } else if (type === "IDAT") {
      idats.push(data);
    } else if (type === "IEND") {
      break;
    }
    offset += 12 + length;
  }
  const channels = colorType === 6 ? 4 : colorType === 2 ? 3 : colorType === 0 ? 1 : 4;
  const raw = zlib.inflateSync(Buffer.concat(idats));
  const stride = width * channels;
  const row = Buffer.alloc(stride);
  const prev = Buffer.alloc(stride);
  let pos = 0;
  let ink = 0;
  let total = 0;
  for (let y = 0; y < height; y += 1) {
    const filter = raw[pos];
    pos += 1;
    const scan = raw.subarray(pos, pos + stride);
    pos += stride;
    for (let i = 0; i < stride; i += 1) {
      const left = i >= channels ? row[i - channels] : 0;
      const up = prev[i];
      const ul = i >= channels ? prev[i - channels] : 0;
      let value = scan[i];
      if (filter === 1) value = (value + left) & 255;
      else if (filter === 2) value = (value + up) & 255;
      else if (filter === 3) value = (value + Math.floor((left + up) / 2)) & 255;
      else if (filter === 4) {
        const paeth = left + up - ul;
        const pa = Math.abs(paeth - left);
        const pb = Math.abs(paeth - up);
        const pc = Math.abs(paeth - ul);
        const pred = pa <= pb && pa <= pc ? left : pb <= pc ? up : ul;
        value = (value + pred) & 255;
      }
      row[i] = value;
    }
    for (let x = 0; x < width; x += 1) {
      const r = row[x * channels];
      const g = channels > 1 ? row[x * channels + 1] : r;
      const b = channels > 2 ? row[x * channels + 2] : r;
      total += 1;
      if (r < 245 || g < 245 || b < 245) ink += 1;
    }
    row.copy(prev);
  }
  return ink / Math.max(total, 1);
}

async function canvasInk(page) {
  const canvas = page.locator("#crystal-graph canvas").first();
  const png = await canvas.screenshot();
  return pngInk(png);
}

async function selectUnitCell(page) {
  const host = page.locator("#display-mode-selector");
  const label = ((await host.innerText()) || "").replace(/\s+/g, " ");
  if (!/^\s*Unit cell\b/i.test(label)) {
    await host.click();
    await page.getByRole("option", { name: "Unit cell", exact: true }).click();
  }
  await page.waitForFunction(() => {
    const node = document.getElementById("display-mode-selector");
    const text = ((node && node.innerText) || "").replace(/\s+/g, " ").trim();
    return text === "Unit cell" || text.startsWith("Unit cell");
  }, null, {timeout: 30000});
}

async function figureRevision(page) {
  return page.evaluate(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    return meta ? Number(meta.render_revision) : NaN;
  });
}

async function unitCellStatus(page, previousRender) {
  return page.evaluate(async (previous) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    if (!scene || !scene.glplot || !scene.camera || !gd.data || !gd.data.length || !meta || !meta.scene_id) {
      return {ok: false, reason: "graph"};
    }
    const render = Number(meta.render_revision);
    if (!Number.isFinite(render) || !(render > Number(previous))) {
      return {ok: false, reason: "revision", render, previous};
    }
    const state = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(meta.scene_id), {cache: "no-store"}).then((response) => response.json());
    const ok = !!(
      state
      && state.display_mode === "unit_cell"
      && Number(state.render_revision) === render
      && Number(state.camera_revision || 0) === Number(meta.camera_revision || 0)
    );
    return {
      ok,
      display: state && state.display_mode,
      projection: state && state.projection,
      render,
      serverRender: state && state.render_revision,
    };
  }, previousRender);
}

async function waitForUnitCellFigure(page, previousRender) {
  // The dropdown updates before the worker paints. A projection click in
  // that gap is overwritten when the new frame arrives with the camera
  // captured before the click.
  const deadline = Date.now() + 60000;
  let last = null;
  while (Date.now() < deadline) {
    last = await unitCellStatus(page, previousRender);
    if (last && last.ok) return last;
    await page.waitForTimeout(200);
  }
  fail(`unit cell figure did not settle ${JSON.stringify(last)}`);
}

async function projectionStatus(page) {
  const view = await readView(page);
  const server = await page.evaluate(async () => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    const id = meta && meta.scene_id ? meta.scene_id : "";
    const state = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(id), {cache: "no-store"}).then((response) => response.json());
    return {projection: state && state.projection, display: state && state.display_mode};
  });
  return {view, server};
}

async function ensureOrthographic(page) {
  // The display-scope callback can still be answering when the new frame
  // paints. Its state snapshot resets the projection radio, so keep
  // applying orthographic until the GL camera, the radio, and the server
  // agree and stay that way.
  let last = null;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    await postAction(page, {action: "projection", type: "orthographic"});
    const deadline = Date.now() + 8000;
    let stableSince = 0;
    while (Date.now() < deadline) {
      last = await projectionStatus(page);
      const ready = last.view.ortho
        && last.view.radio === "orthographic"
        && last.server.projection === "orthographic"
        && last.server.display === "unit_cell";
      if (!ready) stableSince = 0;
      else if (!stableSince) stableSince = Date.now();
      else if (Date.now() - stableSince >= 1500) return last;
      await page.waitForTimeout(200);
    }
  }
  fail(`orthographic camera did not arrive ${JSON.stringify(last)}`);
}

async function leftDrag(page, box) {
  const x = box.x + box.width * 0.5;
  const y = box.y + box.height * 0.5;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 120, y + 70, { steps: 12 });
  await page.mouse.up();
}

async function runOrthoDrag(page) {
  await clickTab(page, "DAP-4");
  const beforeUnitCell = await figureRevision(page);
  await selectUnitCell(page);
  await waitForUnitCellFigure(page, beforeUnitCell);
  await ensureOrthographic(page);
  const rename = await page.locator("#scene-tab-rename-input").inputValue().catch(() => "");
  if (!rename.trim()) {
    log("scene rename input is blank");
  }
  const before = await readView(page);
  const beforeInk = await canvasInk(page);
  if (!before.ortho || before.radio !== "orthographic" || !(before.traces > 0) || beforeInk < CANVAS_INK_FLOOR) {
    fail(`DAP-4 unit cell was already blank ${JSON.stringify({ before, beforeInk, rename })}`);
  }
  const canvas = page.locator("#crystal-graph canvas").first();
  const box = await canvas.boundingBox();
  if (!box) fail("crystal canvas has no box");
  await leftDrag(page, box);
  await rightDrag(page, box);
  await page.mouse.up({ button: "right" });
  await page.waitForTimeout(600);
  const draggedInk = await canvasInk(page);
  const dragged = await readView(page);
  if (!dragged.ortho || dragged.sceneId !== before.sceneId || draggedInk < CANVAS_INK_FLOOR) {
    fail(`orthographic drag hid DAP-4 ${JSON.stringify({ dragged, draggedInk, beforeInk })}`);
  }
  await page.evaluate(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    return window.Plotly.relayout(gd, {
      "scene.camera": {
        eye: { x: 0, y: 0, z: 50 },
        center: { x: 20, y: 20, z: 20 },
        up: { x: 0, y: 0, z: 1 },
        projection: { type: "orthographic" },
      },
    });
  });
  try {
    await page.waitForFunction(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const camera = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene && gd._fullLayout.scene._scene.camera;
      if (!camera || !camera.center || !camera._ortho) return false;
      const center = camera.center;
      const x = Number(center[0] !== undefined ? center[0] : center.x);
      const y = Number(center[1] !== undefined ? center[1] : center.y);
      const z = Number(center[2] !== undefined ? center[2] : center.z);
      return [x, y, z].every(Number.isFinite) && Math.hypot(x, y, z) < 0.5;
    }, null, {timeout: 45000});
  } catch (error) {
    fail(`offscreen orthographic drag was not refit ${JSON.stringify(await readView(page))} (${error.message})`);
  }
  const recoveredInk = await canvasInk(page);
  const recovered = await readView(page);
  if (recoveredInk < CANVAS_INK_FLOOR || !recovered.ortho) {
    fail(`refit left the orthographic canvas blank ${JSON.stringify({ recovered, recoveredInk })}`);
  }
  const stored = await serverCamera(page, recovered.sceneId);
  const storedCenter = stored && stored.center;
  if (storedCenter && Math.hypot(Number(storedCenter.x), Number(storedCenter.y), Number(storedCenter.z)) >= 0.5) {
    fail(`stored camera stayed offscreen ${JSON.stringify(stored)}`);
  }
}

async function runSwitch(page) {
  await graphReady(page);
  log("tab showcase");
  await clickTab(page, "showcase_dap7");
  await wheel(page, -200, 4);
  log("tab dap4");
  await clickTab(page, "DAP-4");
  log("tab nacl");
  await clickTab(page, "ionic_nacl");
  log("upload");
  await page.setInputFiles("#scene-cif-upload-input", uploadPath);
  await page.waitForFunction(() => {
    const status = document.getElementById("upload-status");
    return status && /porous_framework/i.test(status.textContent || "");
  }, { timeout: 90000 });
  await clickTab(page, "porous_framework");
  const view = await readView(page);
  if (!/porous_framework/i.test(view.title) && !/porous_framework/i.test(view.sceneId)) {
    fail(`canvas stayed on ${view.title || view.sceneId || "an unknown scene"} after upload`);
  }
  await page.locator("#view-reset").click();
  await page.waitForTimeout(500);
  const after = await readView(page);
  if (!/porous_framework/i.test(after.title) && !/porous_framework/i.test(after.sceneId)) {
    fail("reset snapped the canvas off the uploaded scene");
  }
}

(async () => {
  log("launch " + mode);
  const browser = await chromium.launch({
    headless: true,
    executablePath: chrome,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--enable-webgl"],
  });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const problems = [];
  page.on("console", (msg) => {
    const text = msg.text();
    if (/Invalid frame header|Too many active WebGL|c\.on is not a function/i.test(text)) problems.push(text);
  });
  page.on("pageerror", (error) => {
    const text = String(error && error.message || error);
    if (/c\.on is not a function|Invalid frame header/i.test(text)) problems.push(text);
  });
  log("goto");
  await page.goto(base, { waitUntil: "domcontentloaded", timeout: 30000 });
  log("graph");
  await graphReady(page);
  log("ready");
  if (mode === "reset") await runReset(page);
  else if (mode === "switch") await runSwitch(page);
  else if (mode === "gestures") await runGestures(page);
  else if (mode === "held") await runHeld(page);
  else if (mode === "rapid") await runRapid(page);
  else if (mode === "sessions") await runSessions(page, browser);
  else if (mode === "leak") await runLeak(page);
  else if (mode === "restart") await runRestart(page);
  else if (mode === "nacl-frame") await runNaclFrame(page);
  else if (mode === "semantic-pick") await runSemanticPick(page);
  else if (mode === "ortho-drag") await runOrthoDrag(page);
  else fail("unknown mode " + mode);
  if (problems.length) fail(problems.slice(0, 8).join("\n"));
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
