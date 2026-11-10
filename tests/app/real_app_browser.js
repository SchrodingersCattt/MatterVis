/* Drive the real MatterVis page through the hand-reproduced bugs.
 * Args: playwright module, chrome path, base url, mode, optional upload path,
 * optional structure, optional restart-ready file.
 */
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");
// Distinct wheel notches. This is input spacing, not a server-state wait.
const WHEEL_EVENT_GAP_MS = 40;
// Pointer sampling while searching for a pickable pixel.
const SCAN_POINTER_GAP_MS = 115;
// Health probe while the restarted process is not accepting connections.
const RESTART_HEALTH_POLL_MS = 300;
// waitForFunction treats an async predicate's Promise as truthy even when
// it eventually resolves false. Await server observations in Node instead.
const waitForServerState = require("./browser_wait");
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
  }, null, { timeout: 90000 });
}

function waitForDashChange(page, propId) {
  return page.waitForResponse((response) => {
    let payload;
    try {
      if (new URL(response.url()).pathname !== "/_dash-update-component") return false;
      payload = response.request().postDataJSON();
    } catch (_) {
      return false;
    }
    const changed = (payload && payload.changedPropIds) || [];
    return changed.some((item) => String(item).indexOf(propId) === 0);
  }, {timeout: 20000});
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
  const before = await page.evaluate(() => {
    const eye = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera.eye;
    return Math.hypot(
      Number(eye[0] !== undefined ? eye[0] : eye.x),
      Number(eye[1] !== undefined ? eye[1] : eye.y),
      Number(eye[2] !== undefined ? eye[2] : eye.z),
    );
  });
  for (let i = 0; i < times; i += 1) {
    await page.mouse.wheel(0, deltaY);
    await page.waitForTimeout(WHEEL_EVENT_GAP_MS);
  }
  await page.waitForFunction((previous) => {
    const eye = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera.eye;
    if (!eye) return false;
    const dist = Math.hypot(
      Number(eye[0] !== undefined ? eye[0] : eye.x),
      Number(eye[1] !== undefined ? eye[1] : eye.y),
      Number(eye[2] !== undefined ? eye[2] : eye.z),
    );
    return Number.isFinite(dist) && Math.abs(dist - previous) / Math.max(previous, 1e-6) > 0.02;
  }, before, {timeout: 20000});
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
  }, null, { timeout: 20000 });
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
  }, null, { timeout: 20000 });
  const firstReset = waitForDashChange(page, "view-reset");
  await page.locator("#view-reset").click();
  await firstReset;
  const secondReset = waitForDashChange(page, "view-reset");
  await page.locator("#view-reset").click();
  await secondReset;
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
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    if (!payload.scene_id && meta && meta.scene_id) payload.scene_id = String(meta.scene_id);
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
  await page.waitForFunction(() => {
    const camera = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera;
    return !!(camera && camera._ortho);
  }, null, {timeout: 20000});
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
  const alignedResponse = waitForDashChange(page, "view-align-a");
  await page.locator("#view-align-a").click();
  await alignedResponse;
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
  const resetResponse = waitForDashChange(page, "view-reset");
  await page.locator("#view-reset").click();
  await resetResponse;
  await page.waitForFunction(() => {
    const camera = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera;
    const radio = document.querySelector("#view-projection input:checked");
    return !!(camera && camera._ortho) && (!radio || radio.value === "orthographic");
  }, null, {timeout: 20000});
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
  // mouse.up resolves after the event turn. A camera relayout scheduled there
  // is visible on the next frame. Plotly's own drag path does not always call
  // the replaced Plotly.relayout, so an empty hold is still a real run: the
  // distance assertion below is unchanged.
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  await page.evaluate(() => { window.__holdDragRelayout = false; });
  const resetResponse = waitForDashChange(page, "view-reset");
  await page.locator("#view-reset").click();
  await resetResponse;
  const reset = await nonblank(page);
  await page.evaluate(() => Promise.all((window.__heldRelease || []).splice(0).map((release) => release())));
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

async function figureIdentity(page) {
  return page.evaluate(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    const camera = scene && scene.camera;
    const eye = camera && camera.eye;
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    const title = gd && gd.layout && gd.layout.title;
    const text = String((title && (title.text || title)) || "");
    const component = (value, index, name) => {
      if (!value) return null;
      const raw = value[index] !== undefined && value[index] !== null ? value[index] : value[name];
      const number = Number(raw);
      return Number.isFinite(number) ? number : null;
    };
    return {
      sceneId: meta && meta.scene_id ? String(meta.scene_id) : "",
      title: text,
      ortho: !!(camera && camera._ortho),
      eye: [
        component(eye, 0, "x"),
        component(eye, 1, "y"),
        component(eye, 2, "z"),
      ],
      render: meta ? {
        scene_id: String(meta.scene_id || ""),
        scene_generation: Number(meta.scene_generation || 0),
        camera_revision: Number(meta.camera_revision || 0),
        render_revision: Number(meta.render_revision || 0),
      } : null,
    };
  });
}

function assertSeparateClients(first, second, label) {
  if (!first || !second || !first.sceneId || !second.sceneId || !first.render || !second.render) {
    fail(`${label} missing figure metadata ${JSON.stringify({first, second})}`);
  }
  if (first.sceneId === second.sceneId || first.render.scene_id === second.render.scene_id) {
    fail(`${label} collapsed onto one scene ${JSON.stringify({first, second})}`);
  }
  if (first.render.scene_id !== first.sceneId || second.render.scene_id !== second.sceneId) {
    fail(`${label} figure metadata does not match the mounted scene ${JSON.stringify({first, second})}`);
  }
  if (!first.eye.every(Number.isFinite) || !second.eye.every(Number.isFinite)) {
    fail(`${label} camera metadata is incomplete ${JSON.stringify({first, second})}`);
  }
}

async function listSceneIds(page) {
  return page.evaluate(async () => {
    const body = await fetch("/api/v2/scenes", {cache: "no-store"}).then((response) => response.json());
    return ((body && body.scenes) || []).map((scene) => String(scene.id));
  });
}

function isUploadPost(response) {
  try {
    const url = new URL(response.url());
    return response.request().method() === "POST" && url.pathname === "/api/v2/upload";
  } catch (_error) {
    return false;
  }
}

async function waitForThisUpload(page, beforeIds) {
  const posted = page.waitForResponse(isUploadPost, {timeout: 90000});
  await page.setInputFiles("#scene-cif-upload-input", uploadPath);
  const response = await posted;
  let meta = {};
  try {
    meta = await response.json();
  } catch (_error) {
    meta = {};
  }
  if (!response.ok()) {
    fail(`upload transaction failed: ${response.status()} ${JSON.stringify(meta)}`);
  }
  const sceneId = meta && meta.scene_id ? String(meta.scene_id) : "";
  const jobId = meta && meta.job_id ? String(meta.job_id) : "";
  if (!sceneId || beforeIds.includes(sceneId)) {
    fail(
      "upload did not acknowledge a new scene_id: "
      + JSON.stringify({meta, beforeIds}),
    );
  }
  if (jobId && meta.status === "pending") {
    await waitForServerState(page, async (wanted) => {
      const body = await fetch(
        "/api/v2/upload/" + encodeURIComponent(wanted.jobId),
        {cache: "no-store"},
      ).then((statusResponse) => statusResponse.json());
      const status = body && body.status;
      const id = body && body.scene_id ? String(body.scene_id) : "";
      const ready = status === "ready" || status === "complete" || status === "success";
      return ready && (!id || id === wanted.sceneId);
    }, {jobId, sceneId}, {timeout: 90000});
  }
  await page.waitForFunction((id) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const render = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    return !!(render && String(render.scene_id) === id && gd.data && gd.data.length);
  }, sceneId, {timeout: 90000});
  const painted = await figureIdentity(page);
  if (painted.sceneId !== sceneId || !painted.render || painted.render.scene_id !== sceneId) {
    fail(`uploaded scene ${sceneId} was not painted on the uploading page ${JSON.stringify(painted)}`);
  }
  return sceneId;
}

async function runSessions(page, browser) {
  const secondProblems = [];
  // Opening another page inside the first client's context would share
  // cookies, storage, and the Dash client identity. Use a new context.
  const context = await browser.newContext({viewport: {width: 1200, height: 800}});
  try {
    const second = await context.newPage();
    second.on("pageerror", (error) => {
      const text = String(error && error.message || error);
      if (/c\.on is not a function|Invalid frame header/i.test(text)) secondProblems.push(text);
    });
    await second.goto(base, {waitUntil: "domcontentloaded", timeout: 30000});
    await graphReady(page);
    await graphReady(second);
    const firstInit = await figureIdentity(page);
    const secondInit = await figureIdentity(second);
    if (!firstInit.sceneId || !firstInit.render || !secondInit.sceneId || !secondInit.render) {
      fail(`clients did not initialize figure metadata ${JSON.stringify({firstInit, secondInit})}`);
    }
    if (!firstInit.eye.every(Number.isFinite) || !secondInit.eye.every(Number.isFinite)) {
      fail(`clients initialized without camera metadata ${JSON.stringify({firstInit, secondInit})}`);
    }
    await clickTab(page, "ionic_nacl");
    await clickTab(second, "molecular_water");
    const firstOwned = await figureIdentity(page);
    const secondOwned = await figureIdentity(second);
    assertSeparateClients(firstOwned, secondOwned, "independently initialized clients");
    let uploadedScene = "";
    if (uploadPath) {
      const beforeIds = await listSceneIds(page);
      uploadedScene = await waitForThisUpload(page, beforeIds);
    }
    const other = await figureIdentity(second);
    if (
      other.sceneId !== secondOwned.sceneId
      || !other.render
      || other.render.scene_id !== secondOwned.sceneId
      || other.render.camera_revision !== secondOwned.render.camera_revision
      || other.ortho !== secondOwned.ortho
    ) {
      fail(`upload on the first page rewrote the second page ${JSON.stringify({secondOwned, other})}`);
    }
    const firstBeforeGesture = await figureIdentity(page);
    const resetResponse = waitForDashChange(page, "view-reset");
    const orthoResponse = waitForDashChange(second, "view-projection");
    await page.locator("#view-reset").click();
    await second.locator("#view-projection input[value='orthographic']").click();
    await resetResponse;
    await orthoResponse;
    await second.waitForFunction(() => {
      const camera = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera;
      return !!(camera && camera._ortho);
    }, null, {timeout: 20000});
    const first = await figureIdentity(page);
    const secondView = await figureIdentity(second);
    await nonblank(page);
    await nonblank(second);
    assertSeparateClients(first, secondView, "two browsers");
    if (first.sceneId !== firstBeforeGesture.sceneId || first.render.scene_id !== firstBeforeGesture.sceneId) {
      fail(`first client lost its scene during the other client's camera change ${JSON.stringify({firstBeforeGesture, first})}`);
    }
    if (uploadedScene && first.sceneId !== uploadedScene) {
      fail(`uploading page left its acknowledged scene ${uploadedScene} for ${first.sceneId}`);
    }
    if (
      !secondView.ortho
      || secondView.sceneId !== secondOwned.sceneId
      || secondView.render.scene_id !== secondOwned.sceneId
      || secondView.render.camera_revision < secondOwned.render.camera_revision
    ) {
      fail(`second client lost its scene or camera ${JSON.stringify({secondOwned, secondView})}`);
    }
    if (secondProblems.length) fail(secondProblems.join("\n"));
  } finally {
    await context.close();
  }
}

async function runCrossClient(page, browser) {
  // Client A visits S and changes its camera. Client B stays on T, then
  // switches to S. B's Plotly graph must show S, not the process-wide
  // snapshot A already classified.
  const context = await browser.newContext({viewport: {width: 1200, height: 800}});
  try {
    const clientB = await context.newPage();
    await clientB.goto(base, {waitUntil: "domcontentloaded", timeout: 30000});
    await graphReady(page);
    await graphReady(clientB);
    await clickTab(page, "ionic_nacl");
    await clickTab(clientB, "molecular_water");
    const sceneS = await figureIdentity(page);
    const sceneT = await figureIdentity(clientB);
    assertSeparateClients(sceneS, sceneT, "cross-client setup");
    const orthoResponse = waitForDashChange(page, "view-projection");
    await page.locator("#view-projection input[value='orthographic']").click();
    await orthoResponse;
    await page.waitForFunction(() => {
      const camera = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera;
      return !!(camera && camera._ortho);
    }, null, {timeout: 20000});
    const changed = await figureIdentity(page);
    if (changed.sceneId !== sceneS.sceneId || !changed.ortho) {
      fail(`client A left scene S during the projection change ${JSON.stringify(changed)}`);
    }
    const stillT = await figureIdentity(clientB);
    if (stillT.sceneId !== sceneT.sceneId || !stillT.render || stillT.render.scene_id !== sceneT.sceneId) {
      fail(`client B left T while A changed S ${JSON.stringify({sceneT, stillT})}`);
    }
    await clickTab(clientB, "ionic_nacl");
    try {
      await waitForServerState(clientB, async (sceneId) => {
        const gd = document.querySelector("#crystal-graph .js-plotly-plot");
        const scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
        const camera = scene && scene.camera;
        const render = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
        const title = gd && gd.layout && gd.layout.title;
        const text = String((title && (title.text || title)) || "").toLowerCase();
        const radio = document.querySelector("#view-projection input:checked");
        if (!camera || !camera._ortho || !render || String(render.scene_id) !== sceneId) return false;
        if (!text.includes("ionic_nacl") && !text.includes("nacl")) return false;
        if (!radio || radio.value !== "orthographic") return false;
        const state = await fetch(
          "/api/v2/state?scene_id=" + encodeURIComponent(sceneId),
          {cache: "no-store"},
        ).then((response) => response.json());
        return String(state.scene_id) === sceneId
          && state.projection === "orthographic"
          && Number(state.render_revision) === Number(render.render_revision)
          && Number(state.camera_revision || 0) === Number(render.camera_revision || 0);
      }, sceneS.sceneId, {timeout: 60000});
    } catch (error) {
      const arrived = await figureIdentity(clientB).catch(() => null);
      const view = await readView(clientB).catch(() => null);
      fail(`client B graph did not adopt S: ${error.message} graph=${JSON.stringify({arrived, view})}`);
    }
  } finally {
    await context.close();
  }
}

async function runLeak(page) {
  await graphReady(page);
  const started = Date.now();
  for (let i = 0; i < 100; i += 1) {
    const name = i % 2 === 0 ? "ionic_nacl" : "molecular_water";
    const before = Date.now();
    await clickTab(page, name);
    if (i % 10 === 9) {
      const census = await page.evaluate(() => ({
        sockets: (window.__mvCensus || {}).socketsOpen || 0,
        webgl: (window.__mvCensus || {}).webglContexts || 0,
        canvases: document.getElementById("crystal-graph")
          ? document.getElementById("crystal-graph").querySelectorAll("canvas").length : 0,
      }));
      log(`leak ${i + 1}/100 ${Date.now() - before}ms elapsed ${Date.now() - started}ms ${JSON.stringify(census)}`);
    }
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
    }, null, { timeout: 90000 });
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
    await page.waitForTimeout(RESTART_HEALTH_POLL_MS);
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
  const pause = (options && options.pause) || SCAN_POINTER_GAP_MS;
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

async function runDragReplot(page) {
  await graphReady(page);
  if (structure) await clickTab(page, structure);
  await page.waitForFunction(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    return !!(gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene && gd._fullLayout.scene._scene.camera);
  });
  const readEye = () => {
    const scene = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene;
    const live = scene && typeof scene.getCamera === "function" ? scene.getCamera() : scene && scene.camera;
    const eye = live && live.eye;
    const component = (value, index, key) => Number(value && value[index] !== undefined ? value[index] : value && value[key]);
    if (!eye) return null;
    return [component(eye, 0, "x"), component(eye, 1, "y"), component(eye, 2, "z")];
  };
  const before = await page.evaluate(readEye);
  const box = await page.locator("#crystal-graph canvas").first().boundingBox();
  if (!box) fail("crystal canvas has no box");
  const starts = [
    [box.x + box.width * 0.45, box.y + box.height * 0.5],
    [box.x + box.width * 0.32, box.y + box.height * 0.62],
    [box.x + box.width * 0.58, box.y + box.height * 0.4],
  ];
  let dragged = null;
  for (const [sx, sy] of starts) {
    await page.mouse.move(sx, sy);
    await page.mouse.down();
    await page.mouse.move(sx + 140, sy + 80, {steps: 12});
    try {
      await page.waitForFunction((previous) => {
        const scene = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene;
        const live = scene && typeof scene.getCamera === "function" ? scene.getCamera() : scene && scene.camera;
        const eye = live && live.eye;
        const component = (value, index, key) => Number(value && value[index] !== undefined ? value[index] : value && value[key]);
        if (!eye) return false;
        const next = [component(eye, 0, "x"), component(eye, 1, "y"), component(eye, 2, "z")];
        const shift = Math.hypot(next[0] - previous[0], next[1] - previous[1], next[2] - previous[2]);
        return Number.isFinite(shift) && shift > 0.02;
      }, before, {timeout: 4000});
      dragged = await page.evaluate(readEye);
      break;
    } catch (error) {
      await page.mouse.up();
    }
  }
  if (!dragged) fail("orbit did not move the live camera");
  // A full Plotly.react is deferred until the press ends, so it cannot be
  // the in-drag snap. This relayout is the replot that finishes while the
  // button is down; its afterplot runs the same restore the frame uses.
  await page.evaluate((original) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    return window.Plotly.relayout(gd, {"scene.camera.eye": {x: original[0], y: original[1], z: original[2]}});
  }, before);
  await page.waitForFunction((orbit) => {
    const scene = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene;
    const live = scene && typeof scene.getCamera === "function" ? scene.getCamera() : scene && scene.camera;
    const eye = live && live.eye;
    const component = (value, index, key) => Number(value && value[index] !== undefined ? value[index] : value && value[key]);
    if (!eye) return false;
    const next = [component(eye, 0, "x"), component(eye, 1, "y"), component(eye, 2, "z")];
    const shift = Math.hypot(next[0] - orbit[0], next[1] - orbit[1], next[2] - orbit[2]);
    return Number.isFinite(shift) && shift < 0.05;
  }, dragged, {timeout: 20000});
  // A new generation must not receive that orbit.
  const replaced = await page.evaluate((original) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const render = gd.layout.meta.mattervis_render;
    render.scene_generation = Number(render.scene_generation || 0) + 1;
    const component = (value, index, key) => Number(value && value[index] !== undefined ? value[index] : value && value[key]);
    return window.Plotly.relayout(gd, {"scene.camera.eye": {x: original[0], y: original[1], z: original[2]}}).then(() => {
      const eye = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera.eye;
      return [component(eye, 0, "x"), component(eye, 1, "y"), component(eye, 2, "z")];
    });
  }, before);
  const stayed = Math.hypot(replaced[0] - before[0], replaced[1] - before[1], replaced[2] - before[2]);
  if (!(stayed < 0.05)) fail("new generation restored the previous drag eye " + JSON.stringify({before, replaced, dragged}));
  await page.mouse.up();
  // The next press has not moved, so it has no sample of its own. Putting
  // the pre-drag eye back must not revive the orbit from the press that ended.
  await page.mouse.down();
  const untouched = await page.evaluate((original) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const component = (value, index, key) => Number(value && value[index] !== undefined ? value[index] : value && value[key]);
    return window.Plotly.relayout(gd, {"scene.camera.eye": {x: original[0], y: original[1], z: original[2]}}).then(() => {
      const eye = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera.eye;
      return [component(eye, 0, "x"), component(eye, 1, "y"), component(eye, 2, "z")];
    });
  }, before);
  await page.mouse.up();
  const leaked = Math.hypot(untouched[0] - dragged[0], untouched[1] - dragged[1], untouched[2] - dragged[2]);
  const held = Math.hypot(untouched[0] - before[0], untouched[1] - before[1], untouched[2] - before[2]);
  if (!(leaked > 0.05 && held < 0.05)) fail("later press restored the previous orbit " + JSON.stringify({before, dragged, untouched}));
}

async function runSemanticPick(page) {
  await graphReady(page);
  if (structure) await clickTab(page, structure);
  await page.waitForFunction(()=>window.MatterVisScenePicking && window.MatterVisSemantic);
  // Native pointer coordinates come from the actual rendered camera. No
  // synthetic plotly_click event can rescue a broken event path here.
  async function targetPixel(kind, exclude) {
    return page.evaluate(({kind,exclude})=>{
      const api=window.MatterVisScenePicking,gd=window.MatterVisSemantic.graph(),idx=api.index(gd);
      if(!idx) return null;
      const graph=document.getElementById('crystal-graph');
      const panel=document.getElementById('mv-selection-appearance');
      const graphRect=graph ? graph.getBoundingClientRect() : null;
      // A docked inspector must not cover the plot. A primitive whose screen
      // point is inside the graph and inside the open panel is a failure,
      // not a point to skip.
      function covered(x, y) {
        if (!panel || panel.hidden || !graphRect) return false;
        const rect = panel.getBoundingClientRect();
        const insideGraph = x >= graphRect.left && x <= graphRect.right && y >= graphRect.top && y <= graphRect.bottom;
        const insidePanel = x > rect.left + 1 && x < rect.right - 1 && y > rect.top + 1 && y < rect.bottom - 1;
        return insideGraph && insidePanel;
      }
      function onCanvas(x, y) {
        const el = document.elementFromPoint(x, y);
        if (!el || !graph || !graph.contains(el)) return false;
        return !(el.closest && el.closest('.mv-canvas-toolbar,.modebar,.mv-hover-inspector'));
      }
      let coveredPoint = null;
      let found = null;
      for(const p of idx.primitives) {
        if(!p.target || p.target.kind!==kind || p.target.id===exclude) continue;
        const q=p.p || (p.type==='bond' ? [(p.a[0]+p.b[0])/2,(p.a[1]+p.b[1])/2] : [(p.a[0]+p.b[0]+p.c[0])/3,(p.a[1]+p.b[1]+p.c[1])/3]);
        if (covered(q[0], q[1])) coveredPoint = {x:q[0], y:q[1], id:p.target.id};
        if(found || !onCanvas(q[0], q[1])) continue;
        const hit=api.hits(gd,q[0],q[1]);
        if(hit && hit[0] && hit[0].id===p.target.id) found = {x:q[0],y:q[1],target:p.target};
      }
      if (coveredPoint) return {covered: coveredPoint};
      return found;
    },{kind,exclude});
  }
  async function selected(ids) {
    await waitForServerState(page, async ids=>{
      const bridge=window.MatterVisSemantic;
      const r=await fetch('/api/v2/selection?scene_id='+encodeURIComponent(bridge.sceneId())).then(r=>r.json());
      const targets=r.selection.targets||[];
      return ids.length===targets.length && ids.every(id=>targets.some(t=>t.id===id));
    },ids,{timeout:20000});
  }
  async function dragAway(x, y, button) {
    let lastError = null;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        await dragAwayOnce(x, y, button);
        return;
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError;
  }
  async function dragAwayOnce(x, y, button) {
    const before = await page.evaluate(() => {
      const camera = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera;
      const eye = camera.eye;
      const center = camera.center || {x: 0, y: 0, z: 0};
      const component = (value, index, key) => Number(value[index] !== undefined ? value[index] : value[key]);
      return {
        eye: [component(eye, 0, "x"), component(eye, 1, "y"), component(eye, 2, "z")],
        center: [component(center, 0, "x"), component(center, 1, "y"), component(center, 2, "z")],
      };
    });
    const delta = await page.evaluate(({x, y}) => {
      const box = document.getElementById('crystal-graph').getBoundingClientRect();
      let dx = 70;
      let dy = 30;
      if (x + dx > box.right - 12) dx = -70;
      if (y + dy > box.bottom - 12) dy = -30;
      if (x + dx < box.left + 12) dx = 40;
      if (y + dy < box.top + 12) dy = 24;
      return {dx, dy};
    }, {x, y});
    await page.mouse.move(x, y);
    await page.mouse.down(button ? {button} : undefined);
    await page.mouse.move(x + delta.dx, y + delta.dy, {steps: 8});
    await page.mouse.up(button ? {button} : undefined);
    await page.waitForFunction((previous) => {
      const camera = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera;
      if (!camera || !camera.eye) return false;
      const component = (value, index, key) => Number(value && value[index] !== undefined ? value[index] : value && value[key]);
      const eye = [component(camera.eye, 0, "x"), component(camera.eye, 1, "y"), component(camera.eye, 2, "z")];
      const center = camera.center || {x: 0, y: 0, z: 0};
      const nextCenter = [component(center, 0, "x"), component(center, 1, "y"), component(center, 2, "z")];
      const eyeShift = Math.hypot(eye[0] - previous.eye[0], eye[1] - previous.eye[1], eye[2] - previous.eye[2]);
      const centerShift = Math.hypot(nextCenter[0] - previous.center[0], nextCenter[1] - previous.center[1], nextCenter[2] - previous.center[2]);
      return [eyeShift, centerShift].every(Number.isFinite) && (eyeShift > 0.02 || centerShift > 0.02);
    }, before, {timeout: 8000});
  }
  let atom=await targetPixel('atom');if(!atom || atom.covered) fail('no visible local atom pick '+JSON.stringify(atom));
  let hoverRequests=0;
  const observer=request=>{if(/selection\/by_box/.test(request.url())) hoverRequests++;};page.on('request',observer);
  await page.mouse.move(atom.x,atom.y);
  await page.waitForSelector('#mv-hover-inspector');
  await page.waitForFunction(label=>document.getElementById('mv-hover-inspector')?.textContent.includes(label),atom.target.label);
  await page.mouse.click(atom.x,atom.y);await selected([atom.target.id]);
  await page.waitForSelector('#mv-selection-appearance:not([hidden])');
  await page.waitForFunction(() => {
    const panel = document.getElementById('mv-selection-appearance');
    const canvas = document.querySelector('#crystal-graph canvas');
    const gd = document.querySelector('#crystal-graph .js-plotly-plot');
    if (!panel || panel.hidden || !canvas || !gd || !gd._fullLayout) return false;
    const layoutWidth = Number(gd._fullLayout.width || 0);
    const canvasBox = canvas.getBoundingClientRect();
    if (!(layoutWidth > 40) || Math.abs(layoutWidth - canvasBox.width) > 4) return false;
    const dock = panel.getBoundingClientRect();
    if (!(dock.width > 40)) return false;
    const overlapW = Math.min(dock.right, canvasBox.right) - Math.max(dock.left, canvasBox.left);
    const overlapH = Math.min(dock.bottom, canvasBox.bottom) - Math.max(dock.top, canvasBox.top);
    return !(overlapW > 1 && overlapH > 1);
  }, null, {timeout: 10000});
  await page.waitForFunction(()=>{const c=document.querySelector('.mv-focus-canvas');return c && c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3 && v>0);});
  const moved=await targetPixel('atom');
  if(!moved || moved.covered || moved.target.id!==atom.target.id) {
    const again=await page.evaluate((id)=>{
      const api=window.MatterVisScenePicking,gd=window.MatterVisSemantic.graph(),idx=api.index(gd);
      const graph=document.getElementById('crystal-graph');
      const primitive=idx.primitives.find((item)=>item.target && item.target.id===id && item.p);
      if(!primitive) return null;
      const el=document.elementFromPoint(primitive.p[0], primitive.p[1]);
      if(!el || !graph || !graph.contains(el)) return null;
      const hit=api.hits(gd, primitive.p[0], primitive.p[1]);
      if(!hit || !hit[0] || hit[0].id!==id) return null;
      return {x:primitive.p[0], y:primitive.p[1], target:primitive.target};
    }, atom.target.id);
    if(!again) fail('open selection dock moved the selected atom off the plot');
    atom=again;
  } else {
    atom=moved;
  }
  const second=await targetPixel('atom',atom.target.id);if(!second || second.covered) fail('selection panel blocked an atom pick '+JSON.stringify(second));
  await page.keyboard.down('Control');await page.mouse.click(second.x,second.y);await page.keyboard.up('Control');await selected([atom.target.id,second.target.id]);
  await page.keyboard.down('Control');await page.mouse.click(second.x,second.y);await page.keyboard.up('Control');await selected([atom.target.id]);
  await page.keyboard.press('Escape');await selected([]);
  // Right click with cleared hover and no pointermove must use click position.
  await page.evaluate(()=>window.MatterVisInvalidateSemanticHover());
  await page.evaluate(hit=>document.getElementById('crystal-graph').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:hit.x,clientY:hit.y,button:2})),atom);
  await selected([atom.target.id]);
  await page.keyboard.press('Escape');await selected([]);
  // Drag ends far enough away to be an orbit, never a selection gesture.
  await dragAway(atom.x, atom.y);await selected([]);
  await dragAway(atom.x, atom.y, "right");await selected([]);
  const afterOrbit=await targetPixel('atom');if(!afterOrbit) fail('picking lost after orbit');
  await page.mouse.click(afterOrbit.x,afterOrbit.y);await selected([afterOrbit.target.id]);
  const inspector=page.locator('#mv-selection-appearance');
  await inspector.getByLabel('Color',{exact:true}).fill('#aa6688');
  await inspector.getByRole('button',{name:'Apply',exact:true}).click();
  await waitForServerState(page, async target=>{const r=await fetch('/api/v2/style_overrides?scene_id='+encodeURIComponent(window.MatterVisSemantic.sceneId())).then(r=>r.json());return r.style_overrides[target.id]?.color==='#aa6688';},afterOrbit.target);
  // A saved exact color survives deselection and a live element-palette edit.
  await page.keyboard.press('Escape');await selected([]);
  const inherited=await page.evaluate(async target=>{
    const bridge=window.MatterVisSemantic,id=bridge.sceneId(),gd=bridge.graph();
    const rows=gd.data.flatMap(t=>t.customdata||[]),sibling=rows.find(row=>Array.isArray(row)&&row[0]==='atom'&&row[3]===target.element&&row[6]!==target.id);
    await fetch('/api/v2/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene_id:id,element_colors:{[target.element]:'#8299ac'}})});
    return sibling ? {kind:'atom',id:sibling[6]} : null;
  },afterOrbit.target);
  if(!inherited) fail('palette fixture needs a same-element sibling');
  await waitForServerState(page, async targets=>{
    const r=await fetch('/api/v2/selection/appearance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene_id:window.MatterVisSemantic.sceneId(),targets})}).then(r=>r.json());
    return r.items[0].effective.color==='#aa6688' && r.items[1].effective.color==='#8299ac';
  },[afterOrbit.target,inherited]);
  const exactPoint=await page.evaluate(target=>{const idx=window.MatterVisScenePicking.index(window.MatterVisSemantic.graph()),p=idx.primitives.find(p=>p.target?.id===target.id);return {x:p.p[0],y:p.p[1]};},afterOrbit.target);
  await page.mouse.click(exactPoint.x,exactPoint.y);await selected([afterOrbit.target.id]);
  await page.waitForFunction(()=>document.querySelector('[aria-label="Inherit Color"]')?.disabled===false);
  await inspector.getByRole('button',{name:'Inherit Color',exact:true}).click();
  await waitForServerState(page, async target=>{const r=await fetch('/api/v2/style_overrides?scene_id='+encodeURIComponent(window.MatterVisSemantic.sceneId())).then(r=>r.json());return !r.style_overrides[target.id]?.color;},afterOrbit.target);
  await waitForServerState(page, async target=>{const body=await fetch('/api/v2/selection/appearance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene_id:window.MatterVisSemantic.sceneId(),targets:[target]})}).then(r=>r.json());return body.items[0].effective.color==='#8299ac';},afterOrbit.target);
  const bond=await targetPixel('bond');if(!bond || bond.covered) fail('selection panel blocked a bond pick '+JSON.stringify(bond && bond.covered ? bond : await page.evaluate(()=>{const g=window.MatterVisSemantic.graph(),idx=window.MatterVisScenePicking.index(g);return {counts:idx.primitives.reduce((a,p)=>(a[p.type]=(a[p.type]||0)+1,a),{}),traces:g._fullData.filter(t=>(t.meta||{}).mv_role==='bond_selection').map(t=>({meta:t.meta,cd:t.customdata?.[0]}))};})));
  await page.keyboard.down('Control');await page.mouse.click(bond.x,bond.y);await page.keyboard.up('Control');await selected([afterOrbit.target.id,bond.target.id]);
  // Rapid motion stays local, keeps at most one overlay and no stale tooltip.
  for(let n=0;n<15;n++) await page.mouse.move(n%2?bond.x:afterOrbit.x,n%2?bond.y:afterOrbit.y);
  if(process.env.MATTERVIS_VISUAL_OUTPUT) await page.locator('#crystal-graph').screenshot({path:process.env.MATTERVIS_VISUAL_OUTPUT+'/molecule-light.png'});
  if(hoverRequests) fail('hover issued '+hoverRequests+' coordinate requests');
  page.off('request',observer);
  if(await page.locator('.mv-focus-canvas').count()!==1) fail('focus overlays leaked');
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
  // A painted tab can precede its sidebar callback. Wait for that scene's
  // controls before reading the dropdown or clicking a value from the old tab.
  await waitForServerState(page, async () => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    if (!meta || !meta.scene_id) return false;
    const state = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(meta.scene_id), {cache: "no-store"}).then((response) => response.json());
    const host = document.getElementById("display-mode-selector");
    const controls = document.getElementById("scene-controls-scene");
    const acknowledged = controls && controls.textContent ? JSON.parse(controls.textContent) : {};
    const label = ((host && host.innerText) || "").replace(/\s+/g, " ").trim();
    return acknowledged.scene_id === meta.scene_id
      && !!label && label.startsWith("Unit cell") === (state.display_mode === "unit_cell");
  }, null, {timeout: 30000});
  const host = page.locator("#display-mode-selector");
  const label = ((await host.innerText()) || "").replace(/\s+/g, " ").trim();
  const changed = !label.startsWith("Unit cell");
  if (changed) {
    await host.click();
    await page.getByRole("option", { name: "Unit cell", exact: true }).click();
  }
  await waitForServerState(page, async () => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    if (!meta || !meta.scene_id) return false;
    const state = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(meta.scene_id), {cache: "no-store"}).then((response) => response.json());
    const host = document.getElementById("display-mode-selector");
    return state.display_mode === "unit_cell" && /^\s*Unit cell\b/.test((host && host.innerText) || "");
  }, null, {timeout: 30000});
  return changed;
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
    return {
      projection: state && state.projection, display: state && state.display_mode,
      render: state && state.render_revision, camera: state && state.camera_revision,
      shown: meta,
      cameraDiag: window.__mv_camera_diag,
      viewDiag: window.__mv_view_diag,
      status: window.__mvRenderStatus,
      queue: gd && gd.__mvRenderQueueState && {
        running: gd.__mvRenderQueueState.running,
        pending: gd.__mvRenderQueueState.items.length,
      },
    };
  });
  return {view, server};
}

async function ensureOrthographic(page) {
  // Issue one command after the unit-cell frame fence. Reposting can hide a
  // dropped camera delivery and used to race the display callback itself.
  await postAction(page, {action: "projection", type: "orthographic"});
  const deadline = Date.now() + 30000;
  let last = null;
  while (Date.now() < deadline) {
    last = await projectionStatus(page);
    if (last.view.ortho
        && last.view.radio === "orthographic"
        && last.server.projection === "orthographic"
        && last.server.display === "unit_cell") return last;
    await page.waitForTimeout(200);
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
  const unitCellChanged = await selectUnitCell(page);
  // If DAP-4 already starts in Unit cell mode there is no new render revision
  // to wait for; require the existing WebGL frame instead of timing out on 0 > 0.
  await waitForUnitCellFigure(page, unitCellChanged ? beforeUnitCell : -1);
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
  await page.waitForFunction((previous) => {
    const camera = document.querySelector("#crystal-graph .js-plotly-plot")._fullLayout.scene._scene.camera;
    if (!camera || !camera._ortho || !camera.eye) return false;
    const eye = camera.eye;
    const center = camera.center || {x: 0, y: 0, z: 0};
    const dist = Math.hypot(
      Number(eye[0] !== undefined ? eye[0] : eye.x),
      Number(eye[1] !== undefined ? eye[1] : eye.y),
      Number(eye[2] !== undefined ? eye[2] : eye.z),
    );
    const cx = Number(center[0] !== undefined ? center[0] : center.x);
    const cy = Number(center[1] !== undefined ? center[1] : center.y);
    const cz = Number(center[2] !== undefined ? center[2] : center.z);
    const pc = previous.center || {x: 0, y: 0, z: 0};
    const centerShift = Math.hypot(cx - Number(pc.x || 0), cy - Number(pc.y || 0), cz - Number(pc.z || 0));
    const zoom = Math.abs(dist - Number(previous.dist)) / Math.max(Number(previous.dist), 1e-6);
    return centerShift > 0.02 || zoom > 0.02;
  }, {dist: before.dist, center: before.center}, {timeout: 20000});
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
    fail(`offscreen orthographic drag was not refit ${JSON.stringify(await projectionStatus(page))} (${error.message})`);
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
  }, null, { timeout: 90000 });
  await clickTab(page, "porous_framework");
  const view = await readView(page);
  if (!/porous_framework/i.test(view.title) && !/porous_framework/i.test(view.sceneId)) {
    fail(`canvas stayed on ${view.title || view.sceneId || "an unknown scene"} after upload`);
  }
  const resetResponse = waitForDashChange(page, "view-reset");
  await page.locator("#view-reset").click();
  await resetResponse;
  await page.waitForFunction((sceneId) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    return !!(meta && String(meta.scene_id) === sceneId && gd.data && gd.data.length);
  }, (await readView(page)).sceneId, {timeout: 20000});
  const after = await readView(page);
  if (!/porous_framework/i.test(after.title) && !/porous_framework/i.test(after.sceneId)) {
    fail("reset snapped the canvas off the uploaded scene");
  }
}

async function commitSceneRename(page, label) {
  // Duplicate paints the canvas title before Dash fills the rename box, and
  // a poll that repeats the current label can land between fill() and click().
  // Set the value and click in one turn so the callback snapshots this label.
  await page.evaluate((next) => {
    const button = document.getElementById("scene-rename-btn");
    if (!button) return;
    if (window.dash_clientside && typeof window.dash_clientside.set_props === "function") {
      window.dash_clientside.set_props("scene-tab-rename-input", {value: next});
    }
    const input = document.getElementById("scene-tab-rename-input");
    if (input) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(input, next);
      input.dispatchEvent(new Event("input", {bubbles: true}));
      input.dispatchEvent(new Event("change", {bubbles: true}));
    }
    button.click();
  }, label);
}

async function runSceneControls(page) {
  // Exercise the high-risk scene-tab path end to end: duplicate must create
  // and select a new tab, rename must repaint the canvas title, and an empty
  // label must be rejected without changing the active tab.
  await graphReady(page);
  const before = await readView(page);
  const tabs = page.locator("#scene-tabs .scene-tab-text");
  const beforeCount = await tabs.count();
  const duplicate = page.locator("#scene-new-tab-btn");
  if (!(await duplicate.isVisible())) fail("duplicate scene control is not visible");
  const aria = await duplicate.getAttribute("aria-label");
  if (aria !== "Duplicate active scene as new tab") fail(`duplicate control lacks accessible label: ${aria}`);
  await duplicate.click();
  await page.waitForFunction((count) => {
    return document.querySelectorAll("#scene-tabs .scene-tab-text").length === count + 1;
  }, beforeCount, {timeout: 30000});
  await page.waitForFunction(() => {
    return [...document.querySelectorAll("#scene-tabs .scene-tab-text")]
      .some((node) => /copy/i.test(node.textContent || ""));
  }, null, {timeout: 30000});
  try {
    await page.waitForFunction(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const title = gd && gd.layout && gd.layout.title;
      const text = title && (title.text || title);
      return /copy/i.test(String(text || ""));
    }, null, {timeout: 45000});
  } catch (error) {
    let diagnostics = null;
    try {
      diagnostics = await page.evaluate(() => (
        typeof window.__mattervisFigureDiagnostics === "function"
          ? window.__mattervisFigureDiagnostics()
          : null
      ));
    } catch (_) {}
    fail(`duplicate canvas title did not settle: ${JSON.stringify(diagnostics)} (${error.message})`);
  }
  const duplicateView = await readView(page);
  if (!/copy/i.test(duplicateView.title || "")) {
    fail(`duplicate did not select/repaint the copy tab: ${JSON.stringify({before, duplicateView})}`);
  }

  // The canvas title can arrive over the figure channel before Dash fills
  // the copy's controls. Type only once the rename box holds that label;
  // an earlier keystroke is replaced by the tab-switch response.
  const renameInput = page.locator("#scene-tab-rename-input");
  await page.waitForFunction(() => {
    const node = document.getElementById("scene-tab-rename-input");
    return !!node && /copy/i.test(node.value || "");
  }, null, {timeout: 30000});
  const renamed = `QA scene ${Date.now()}`;
  await commitSceneRename(page, renamed);
  try {
    await page.waitForFunction((name) => {
      const labels = [...document.querySelectorAll("#scene-tabs .scene-tab-text")]
        .map((node) => node.textContent || "");
      const title = document.querySelector("#crystal-graph .js-plotly-plot");
      const text = title && title.layout && title.layout.title && (title.layout.title.text || title.layout.title);
      const loaded = (document.getElementById("upload-status") || {}).textContent || "";
      const footer = (document.getElementById("mv-status-structure") || {}).textContent || "";
      return labels.some((label) => label.includes(name))
        && String(text || "").includes(name)
        && loaded.includes(name)
        && footer.includes(name);
    }, renamed, {timeout: 45000});
  } catch (error) {
    let diagnostics = null;
    try {
      diagnostics = await page.evaluate(() => {
        const gd = document.querySelector("#crystal-graph .js-plotly-plot");
        const title = gd && gd.layout && gd.layout.title;
        return {
          labels: [...document.querySelectorAll("#scene-tabs .scene-tab-text")].map((node) => node.textContent || ""),
          title: String(title && (title.text || title) || ""),
          rename: (document.getElementById("scene-tab-rename-input") || {}).value || "",
          banner: (document.getElementById("status-banner") || {}).textContent || "",
          loaded: (document.getElementById("upload-status") || {}).textContent || "",
          footer: (document.getElementById("mv-status-structure") || {}).textContent || "",
          render: gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render,
          cameraClock: gd && {scope: gd.__mvCameraRevisionScope, epoch: gd.__mvCameraRevisionEpoch, revision: gd.__mvCameraRevision},
          expected: gd && window.MatterVisExpectedRender && window.MatterVisExpectedRender(gd.layout?.meta?.mattervis_render?.scene_id),
          frames: window.__mattervisFigureDiagnostics && window.__mattervisFigureDiagnostics(),
          updates: window.__mv_view_diag,
          camera: window.__mv_camera_diag,
          renderStatus: window.__mvRenderStatus,
        };
      });
    } catch (_) {}
    fail(`rename did not settle: ${JSON.stringify(diagnostics)} (${error.message})`);
  }
  const afterRename = await readView(page);
  const chrome = await page.evaluate((name) => ({
    loaded: (document.getElementById("upload-status") || {}).textContent || "",
    footer: (document.getElementById("mv-status-structure") || {}).textContent || "",
    name,
  }), renamed);
  if (!afterRename.title.includes(renamed) || !chrome.loaded.includes(renamed) || !chrome.footer.includes(renamed)) {
    fail(`scene labels diverged after rename: ${JSON.stringify({title: afterRename.title, ...chrome})}`);
  }

  await commitSceneRename(page, "   ");
  await page.waitForFunction(() => /scene action failed:.*cannot be empty/i.test(
    document.getElementById("status-banner")?.textContent || ""
  ), null, {timeout: 30000});
  const afterEmpty = await readView(page);
  const still = await page.evaluate(() => ({
    loaded: (document.getElementById("upload-status") || {}).textContent || "",
    footer: (document.getElementById("mv-status-structure") || {}).textContent || "",
  }));
  if (!afterEmpty.title.includes(renamed) || !still.loaded.includes(renamed) || !still.footer.includes(renamed)) {
    fail(`empty rename changed a visible label: ${JSON.stringify({title: afterEmpty.title, ...still})}`);
  }
}

async function runSurfacePick(page) {
  await graphReady(page);
  if(structure) await clickTab(page,structure);
  const original=await page.evaluate(()=>fetch('/api/v2/state?scene_id='+encodeURIComponent(window.MatterVisSemantic.sceneId())).then(r=>r.json()));
  try {
  const entities=[
    {id:'surface-left',name:'Left mesh',vertices:[[-6,-6,0],[-1,-6,0],[-1,-1,0],[-6,-1,0]],faces:[[0,1,2],[0,2,3]],color:'#927d9e',opacity:1},
    {id:'surface-right',name:'Right mesh',vertices:[[1,-6,0],[6,-6,0],[6,-1,0],[1,-1,0]],faces:[[0,1,2],[0,2,3]],color:'#75949a',opacity:.6}
  ];
  await page.evaluate(async entities=>{
    const id=window.MatterVisSemantic.sceneId();
    const response=await fetch('/api/v2/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene_id:id,geometry_entities:entities,
      atom_groups:[{id:'hide-atoms',selector:{all:true},visible:false}],
      bfdh_morphology:{enabled:true,scale:2,opacity:.7,facets:[
        {miller:[1,0,0],centroid:[-1,0,0],triangles:[[[-3,0,0],[0,0,0],[-3,3,0]]]},
        {miller:[0,1,0],centroid:[1,0,0],triangles:[[[1,0,0],[4,0,0],[4,3,0]]]}
      ]}})});
    if(!response.ok) throw Error(await response.text());
  },entities);
  await page.waitForFunction(() => {
    const api = window.MatterVisScenePicking;
    const graph = window.MatterVisSemantic && window.MatterVisSemantic.graph();
    const index = api && graph && api.index(graph);
    return !!(index && index.primitives.some((primitive) => primitive.target && primitive.target.id === "surface-left"));
  }, null, {timeout: 30000});
  const snapshots=[];
  async function pixel(kind,id){return page.evaluate(({kind,id})=>{
    const api=window.MatterVisScenePicking,g=window.MatterVisSemantic.graph(),idx=api.index(g);
    for(const p of idx.primitives){if(p.type!=='triangle'||p.target?.kind!==kind||(id&&p.target.id!==id)) continue;
      const x=(p.a[0]+p.b[0]+p.c[0])/3,y=(p.a[1]+p.b[1]+p.c[1])/3;
      const h=api.hits(g,x,y);if(h?.[0]?.id===p.target.id) return {x,y,target:p.target};
    }return null;
  },{kind,id});}
  for(const id of ['surface-left','surface-right']){
    const hit=await pixel('geometry',id);if(!hit) fail('surface face not pickable: '+id);
    await page.evaluate(hit=>document.getElementById('crystal-graph').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:hit.x,clientY:hit.y,button:2})),hit);
    await page.waitForFunction(id=>window.MatterVisSemantic.selected()[0]?.id===id,id,{timeout:3000}).catch(async error=>{fail(error.message+JSON.stringify(await page.evaluate(({x,y})=>({debug:document.getElementById('crystal-graph').__mvContextDebug,bound:document.getElementById('crystal-graph').dataset.rcmBound,selected:window.MatterVisSemantic.selected(),hits:window.MatterVisScenePicking.hits(window.MatterVisSemantic.graph(),x,y)}),hit)));});
    snapshots.push(hit.target);
  }
  const facet=await pixel('facet');if(!facet) fail('BFDH face not pickable');
  if(!Array.isArray(facet.target.miller)) fail('facet lost Miller metadata');
  await page.mouse.click(facet.x,facet.y);
  await page.waitForFunction(id=>window.MatterVisSemantic.selected()[0]?.id===id,facet.target.id);
  await page.keyboard.press('Escape');
  // Camera projection changes retain the semantic mesh identity and local hit index.
  await waitForServerState(page, async()=>{const g=window.MatterVisSemantic.graph(),state=await fetch('/api/v2/state?scene_id='+encodeURIComponent(window.MatterVisSemantic.sceneId())).then(r=>r.json());return !state.selection.targets.length && Number(g.layout.meta.mattervis_render?.render_revision||0)>=Number(state.render_revision||0);});
  await page.evaluate(async()=>{const id=window.MatterVisSemantic.sceneId(),state=await fetch('/api/v2/state?scene_id='+encodeURIComponent(id)).then(r=>r.json());await fetch('/api/v2/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene_id:id,projection:'orthographic',camera_revision:Number(state.camera_revision||0)+1,camera:{eye:{x:1.25,y:1.25,z:1.25},up:{x:0,y:0,z:1},center:{x:0,y:0,z:0},projection:{type:'orthographic'}}})});});
  await page.locator('#view-projection input[value=orthographic]').check();
  // Drive the same camera owner used by delivered view updates; this gate
  // verifies picking after the projection transition, independently of HTTP polling.
  await page.evaluate(()=>window.MatterVisViewCamera.applyAsync({eye:{x:1.25,y:1.25,z:1.25},up:{x:0,y:0,z:1},center:{x:0,y:0,z:0},projection:{type:'orthographic'}},{scene_id:window.MatterVisSemantic.sceneId()}));
  await page.waitForFunction(()=>window.MatterVisSemantic.graph()._fullLayout.scene._scene.camera._ortho,null,{timeout:5000}).catch(async error=>fail(error.message+JSON.stringify(await projectionStatus(page))));
  let ortho=null;
  const orthoDeadline=Date.now()+20000;
  while(Date.now()<orthoDeadline && !ortho){
    ortho=await pixel('geometry','surface-left');
    if(!ortho) await page.waitForTimeout(100);
  }
  if(!ortho) fail('orthographic mesh face not pickable '+JSON.stringify(await page.evaluate(()=>{const g=window.MatterVisSemantic.graph(),idx=window.MatterVisScenePicking.index(g);return {camera:g.layout.scene.camera,traces:g.data.map(t=>t.meta?.geometry_id),primitives:idx.primitives.slice(0,3),rect:idx.rect};})));
  await page.mouse.click(ortho.x,ortho.y);
  if(process.env.MATTERVIS_VISUAL_OUTPUT){
    await page.waitForFunction(id=>window.MatterVisSemantic.selected()[0]?.id===id,ortho.target.id,{timeout:20000});
    await page.locator('#crystal-graph').screenshot({path:process.env.MATTERVIS_VISUAL_OUTPUT+'/surfaces-light.png'});
    await page.evaluate(async()=>{await fetch('/api/v2/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene_id:window.MatterVisSemantic.sceneId(),background:'#202833'})});});
    await page.waitForFunction(()=>window.MatterVisSemantic.graph().layout.paper_bgcolor==='#202833');
    await page.mouse.move(ortho.x,ortho.y);
    await page.locator('#crystal-graph').screenshot({path:process.env.MATTERVIS_VISUAL_OUTPUT+'/surfaces-dark.png'});
  }
  // Measure actual camera-aware queries and overlay painting, with the live
  // Plotly data and no server operation in the timed section.
  const timing=await page.evaluate(()=>{
    const g=window.MatterVisSemantic.graph(),api=window.MatterVisScenePicking,idx=api.index(g),r=idx.rect,times=[];
    for(let i=0;i<150;i++) {const x=r.left+r.width*(i%15)/15,y=r.top+r.height*Math.floor(i/15)/10,t=performance.now();const hit=api.hits(g,x,y);api.paint(g,window.MatterVisSemantic.selected(),hit[0]);times.push(performance.now()-t);}
    times.sort((a,b)=>a-b);return {median_ms:times[75],p95_ms:times[142],traces:g.data.length,primitives:idx.primitives.length,estimated_index_bytes:JSON.stringify(idx.primitives).length,browser:navigator.userAgent};
  });
  console.log('surface-picking measurement '+JSON.stringify(timing));
  const dense=await page.evaluate(async()=>{
    const gd=window.MatterVisSemantic.graph(),n=5000,x=[],y=[],z=[],cd=[];
    for(let i=0;i<n;i++){x.push((i%25-12)*.8);y.push((Math.floor(i/25)%20-10)*.8);z.push((Math.floor(i/500)-5)*.8);cd.push(['atom',i,'C'+i,'C',0,'benchmark','atom:benchmark:'+i,i,'','','']);}
    const mesh={type:'mesh3d',x:[],y:[],z:[],i:[],j:[],k:[],color:'#a69bb2',opacity:.4,meta:{kind:'polyhedron',face_ids:[],spec_id:'benchmark'}};
    for(let t=0;t<40;t++){
      const c=[(t%8-4)*2.8,(Math.floor(t/8)-2)*2.8,0],base=mesh.x.length;
      [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]].forEach(p=>{mesh.x.push(c[0]+p[0]);mesh.y.push(c[1]+p[1]);mesh.z.push(c[2]+p[2]);});
      [[0,2,4],[2,1,4],[1,3,4],[3,0,4],[2,0,5],[1,2,5],[3,1,5],[0,3,5]].forEach(p=>{mesh.i.push(base+p[0]);mesh.j.push(base+p[1]);mesh.k.push(base+p[2]);mesh.meta.face_ids.push('polyhedron:benchmark:'+t);});
    }
    await Plotly.addTraces(gd,[{type:'scatter3d',mode:'markers',x,y,z,customdata:cd,marker:{size:3,color:'#8d959e'},meta:{mv_role:'atom_selection',mv_radii:Array(n).fill(.15)}},mesh]);
    const api=window.MatterVisScenePicking,start=performance.now();api.invalidate(gd);const idx=api.index(gd),build=performance.now()-start,r=idx.rect,times=[];
    for(let i=0;i<150;i++){const primitive=idx.primitives[(i*37)%idx.primitives.length],point=primitive.p||[(primitive.a[0]+primitive.b[0]+primitive.c[0])/3,(primitive.a[1]+primitive.b[1]+primitive.c[1])/3],t=performance.now(),hits=api.hits(gd,point[0],point[1]);api.paint(gd,[],hits[0]);times.push(performance.now()-t);}
    times.sort((a,b)=>a-b);
    return {atoms:n,polyhedra:40,index_build_ms:build,median_ms:times[75],p95_ms:times[142],traces:gd.data.length,primitives:idx.primitives.length,estimated_index_bytes:JSON.stringify(idx.primitives).length};
  });
  console.log('dense-picking measurement '+JSON.stringify(dense));
  } finally { await page.evaluate(async state=>{await fetch('/api/v2/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...state,geometry_entities:state.geometry_entities||[],bfdh_morphology:state.bfdh_morphology||{enabled:false},atom_groups:state.atom_groups||[]})});},original); }
}


function luminance(color) {
  const match = String(color || "").match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!match) return null;
  const red = Number(match[1]);
  const green = Number(match[2]);
  const blue = Number(match[3]);
  return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255;
}

async function themeSample(page) {
  return page.evaluate(() => {
    function channel(color) {
      const match = String(color || "").match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
      return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
    }
    const rootStyle = getComputedStyle(document.documentElement);
    const side = getComputedStyle(document.getElementById("left-panel"));
    const center = getComputedStyle(document.getElementById("center-panel"));
    const radio = document.querySelector("#view-projection input[type='radio']");
    const checkbox = document.querySelector("#display-options input[type='checkbox']");
    const control = radio || checkbox;
    let extension = document.getElementById("mv-extension-panels");
    let created = false;
    if (!extension) {
      extension = document.createElement("aside");
      extension.id = "mv-extension-panels";
      (document.getElementById("viewer-root") || document.body).appendChild(extension);
      created = true;
    }
    const extensionStyle = getComputedStyle(extension);
    const sample = {
      scheme: rootStyle.colorScheme,
      controlScheme: control ? getComputedStyle(control).colorScheme : "",
      side: side.backgroundColor,
      center: center.backgroundColor,
      extension: extensionStyle.backgroundColor,
      controlKind: control ? control.type : "",
    };
    if (created) extension.remove();
    return sample;
  }).then((sample) => ({
    ...sample,
    sideLum: luminance(sample.side),
    centerLum: luminance(sample.center),
    extensionLum: luminance(sample.extension),
  }));
}

function assertTheme(sample, dark) {
  const scheme = dark ? "dark" : "light";
  if (sample.scheme !== scheme || sample.controlScheme !== scheme) {
    fail(`native controls and document color-scheme diverged ${JSON.stringify(sample)}`);
  }
  const surfaces = [sample.sideLum, sample.centerLum, sample.extensionLum];
  if (surfaces.some((value) => value === null)) fail(`theme surface was not a color ${JSON.stringify(sample)}`);
  if (dark && surfaces.some((value) => value >= 0.45)) {
    fail(`dark preference left a pale workbench ${JSON.stringify(sample)}`);
  }
  if (!dark && surfaces.some((value) => value <= 0.75)) {
    fail(`light preference left a dark workbench ${JSON.stringify(sample)}`);
  }
}

async function runTheme(page) {
  await graphReady(page);
  await page.emulateMedia({colorScheme: "dark"});
  assertTheme(await themeSample(page), true);
  await page.evaluate(() => document.documentElement.setAttribute("data-mattervis-theme", "light"));
  assertTheme(await themeSample(page), false);
  await page.evaluate(() => document.documentElement.setAttribute("data-mattervis-theme", "dark"));
  await page.emulateMedia({colorScheme: "light"});
  assertTheme(await themeSample(page), true);
  await page.evaluate(() => document.documentElement.removeAttribute("data-mattervis-theme"));
  assertTheme(await themeSample(page), false);
}

let activeBrowser;
let activeContext;
let tracingStarted = false;
const consoleEvents = [];
const networkEvents = [];
(async () => {
  log("launch " + mode);
  const browser = await chromium.launch({
    headless: true,
    executablePath: chrome,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--enable-webgl"],
  });
  activeBrowser = browser;
  const context = await browser.newContext({viewport: {width: 1400, height: 900}});
  activeContext = context;
  const artifactDir = process.env.MATTERVIS_BROWSER_ARTIFACTS || "";
  let tracing = false;
  if (artifactDir) {
    fs.mkdirSync(artifactDir, {recursive: true});
    try {
      // Screenshot traces and DOM snapshots together make each WebGL
      // Plotly.react pay for a frame capture. On one core that stretches a
      // scene switch from under a second to about two, so 100 switches miss
      // the process budget before the socket census runs. The census keeps
      // DOM snapshots. A failed run still writes its own page screenshot.
      const screenshots = mode !== "leak";
      await context.tracing.start({screenshots, snapshots: true});
      tracing = true;
      tracingStarted = true;
    } catch (error) {
      log("tracing unavailable " + error.message);
    }
  }
  const problems = [];
  function watch(target) {
    target.on("console", (msg) => {
      const text = msg.text();
      consoleEvents.push({type: msg.type(), text});
      if (/Invalid frame header|Too many active WebGL|c\.on is not a function/i.test(text)) problems.push(text);
    });
    target.on("pageerror", (error) => {
      const text = String(error && error.message || error);
      consoleEvents.push({type: "pageerror", text});
      if (/c\.on is not a function|Invalid frame header/i.test(text)) problems.push(text);
    });
    target.on("requestfailed", (request) => {
      networkEvents.push({url: request.url(), failure: request.failure() && request.failure().errorText});
    });
    target.on("response", (response) => {
      if (response.status() >= 400) networkEvents.push({url: response.url(), status: response.status()});
    });
  }
  context.on("page", watch);
  const page = await context.newPage();
  if (mode === "mixed-projection-http") {
    await page.addInitScript(() => { window.MATTERVIS_WS_FIGURE = false; });
  }
  log("goto");
  await page.goto(base, { waitUntil: "domcontentloaded", timeout: 30000 });
  log("graph");
  await graphReady(page);
  log("ready");
  if (mode === "reset") await runReset(page);
  else if (mode === "switch") await runSwitch(page);
  else if (mode === "scene-controls") await runSceneControls(page);
  else if (mode === "gestures") await runGestures(page);
  else if (mode === "held") await runHeld(page);
  else if (mode === "rapid") await runRapid(page);
  else if (mode === "sessions") await runSessions(page, browser);
  else if (mode === "cross-client") await runCrossClient(page, browser);
  else if (mode === "leak") await runLeak(page);
  else if (mode === "restart") await runRestart(page);
  else if (mode === "nacl-frame") await runNaclFrame(page);
  else if (mode === "surface-pick") await runSurfacePick(page);
  else if (mode === "semantic-pick") await runSemanticPick(page);
  else if (mode === "theme") await runTheme(page);
  else if (mode === "drag-replot") await runDragReplot(page);
  else if (mode === "ortho-drag") await runOrthoDrag(page);
  else if (mode === "mixed-projection" || mode === "mixed-projection-http") await require("./projection_mixed_browser")(page, clickTab, waitForServerState);
  else if (mode === "projection-radio") await require("./projection_radio_browser")(page, clickTab, waitForServerState);
  else if (mode === "narrow-layout") await require("./narrow_layout_browser")(page, clickTab);
  else if (mode === "formula-unit-transforms") await require("./transform_formula_unit_browser")(page, clickTab, waitForServerState);
  else fail("unknown mode " + mode);
  if (problems.length) fail(problems.slice(0, 8).join("\n"));
  if (tracing) await context.tracing.stop();
  await context.close();
  await browser.close();
})().catch(async (error) => {
  console.error(error);
  const artifactDir = process.env.MATTERVIS_BROWSER_ARTIFACTS || "";
  if (artifactDir && activeContext) {
    fs.mkdirSync(artifactDir, {recursive: true});
    fs.writeFileSync(path.join(artifactDir, "error.txt"), String(error && error.stack || error));
    fs.writeFileSync(path.join(artifactDir, "console.json"), JSON.stringify(consoleEvents, null, 2));
    fs.writeFileSync(path.join(artifactDir, "network.json"), JSON.stringify(networkEvents, null, 2));
    const pages = activeContext.pages();
    for (let index = 0; index < pages.length; index += 1) {
      const current = pages[index];
      if (current.isClosed()) continue;
      try {
        await current.screenshot({path: path.join(artifactDir, `page-${index}.png`), fullPage: true});
      } catch (shotError) {
        fs.writeFileSync(path.join(artifactDir, `page-${index}-screenshot-error.txt`), String(shotError));
      }
      try {
        const diagnostics = await current.evaluate(async () => {
          async function read(url) {
            try {
              const response = await fetch(url, {cache: "no-store"});
              return await response.json();
            } catch (fetchError) {
              return {error: String(fetchError)};
            }
          }
          const gd = document.querySelector("#crystal-graph .js-plotly-plot");
          return {
            figure: typeof window.__mattervisFigureDiagnostics === "function"
              ? window.__mattervisFigureDiagnostics() : null,
            renderStatus: window.__mvRenderStatus || null,
            viewDiag: window.__mv_view_diag || null,
            cameraDiag: window.__mv_camera_diag || null,
            scenes: await read("/api/v2/scenes"),
            state: await read("/api/v2/state"),
            render: await read("/_test/render-status"),
            meta: (gd && gd.layout && gd.layout.meta) || null,
            title: (gd && gd.layout && gd.layout.title) || null,
          };
        });
        fs.writeFileSync(
          path.join(artifactDir, `page-${index}-diagnostics.json`),
          JSON.stringify(diagnostics, null, 2),
        );
      } catch (diagError) {
        fs.writeFileSync(path.join(artifactDir, `page-${index}-diagnostics-error.txt`), String(diagError));
      }
    }
    if (tracingStarted) {
      try {
        await activeContext.tracing.stop({path: path.join(artifactDir, "trace.zip")});
      } catch (traceError) {
        fs.writeFileSync(path.join(artifactDir, "trace-error.txt"), String(traceError));
      }
    }
  }
  if (activeContext) {
    try { await activeContext.close(); } catch (_) {}
  }
  if (activeBrowser) {
    try { await activeBrowser.close(); } catch (_) {}
  }
  process.exit(1);
});
