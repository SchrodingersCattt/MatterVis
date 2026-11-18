/* Reset, drag, and return. The painted Plotly camera and the server camera
 * must both be the acknowledged pose. A later pan, zoom, and projection
 * do the same. An off-screen camera still refits. */
module.exports = async function runCameraRoundtrip(page, clickTab, waitForServerState) {
  function fail(message) {
    throw new Error(message);
  }
  async function sceneId(page) {
    return page.evaluate(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
      return meta && meta.scene_id ? String(meta.scene_id) : "";
    });
  }
  async function serverCamera(page, id) {
    return page.evaluate(async (scene) => {
      const data = await (await fetch("/api/v2/state?scene_id=" + encodeURIComponent(scene), {cache: "no-store"})).json();
      return {
        eye: data.camera && data.camera.eye,
        center: data.camera && data.camera.center,
        up: data.camera && data.camera.up,
        projection: data.projection,
        revision: data.camera_revision,
      };
    }, id);
  }
  async function painted(page) {
    return page.evaluate(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const camera = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene && gd._fullLayout.scene._scene.camera;
      if (!camera || !camera.eye) return null;
      const num = (value, index, name) => Number(value[index] !== undefined && value[index] !== null ? value[index] : value[name]);
      const vec = (value) => ({x: num(value, 0, "x"), y: num(value, 1, "y"), z: num(value, 2, "z")});
      return {
        eye: vec(camera.eye),
        center: vec(camera.center),
        up: vec(camera.up),
        ortho: !!camera._ortho,
        revision: Number((gd.layout.meta.mattervis_render || {}).camera_revision || 0),
      };
    });
  }
  function close(left, right) {
    if (!left || !right) return false;
    return ["x", "y", "z"].every((name) => Math.abs(Number(left[name]) - Number(right[name])) <= 0.08);
  }
  async function waitPaintedMatches(page, wanted, label) {
    const deadline = Date.now() + 20000;
    let last = null;
    while (Date.now() < deadline) {
      last = await painted(page);
      if (last && close(last.eye, wanted.eye) && close(last.center, wanted.center)) return last;
      await page.waitForTimeout(200);
    }
    fail(`${label} painted ${JSON.stringify(last)} wanted ${JSON.stringify(wanted)}`);
  }
  async function roundTrip(page) {
    await clickTab(page, "molecular_water");
    await clickTab(page, "ionic_nacl");
    await page.waitForFunction(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
      const title = gd && gd.layout && gd.layout.title;
      const text = String((title && (title.text || title)) || "");
      return /ionic_nacl/i.test(text) || /ionic_nacl/i.test(String(meta && meta.scene_id || ""));
    }, null, {timeout: 60000});
  }

  await clickTab(page, "ionic_nacl");
  await page.waitForFunction(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    return !!(scene && scene.glplot && scene.camera);
  }, null, {timeout: 90000});
  await page.locator("#canvas-view-reset").click();
  const id = await sceneId(page);
  if (!id) fail("ionic_nacl scene id missing after reset");
  await waitForServerState(page, async (scene) => {
    const data = await (await fetch("/api/v2/state?scene_id=" + encodeURIComponent(scene), {cache: "no-store"})).json();
    return !!(data.camera && data.camera.eye);
  }, id);
  const reset = await serverCamera(page, id);
  await waitPaintedMatches(page, reset, "reset");
  await page.waitForFunction((revision) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    return !!(meta && Number(meta.camera_revision) === Number(revision));
  }, reset.revision, {timeout: 20000});

  const canvas = page.locator("#crystal-graph canvas").first();
  const box = await canvas.boundingBox();
  if (!box) fail("plot canvas has no box");
  await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.48);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.62 - 80, box.y + box.height * 0.48 - 46, {steps: 12});
  await page.mouse.up();
  await waitForServerState(page, async (payload) => {
    const data = await (await fetch("/api/v2/state?scene_id=" + encodeURIComponent(payload.id), {cache: "no-store"})).json();
    const eye = data.camera && data.camera.eye;
    if (!eye) return false;
    const moved = Math.hypot(eye.x - payload.eye.x, eye.y - payload.eye.y, eye.z - payload.eye.z) > 0.05;
    return moved && Number(data.camera_revision) === Number(payload.revision);
  }, {id, eye: reset.eye, revision: reset.revision});
  const orbit = await serverCamera(page, id);
  if (Number(orbit.revision) !== Number(reset.revision)) {
    fail(`drag bumped camera_revision ${reset.revision} -> ${orbit.revision}`);
  }
  await roundTrip(page);
  const afterOrbit = await serverCamera(page, id);
  if (!close(afterOrbit.eye, orbit.eye) || !close(afterOrbit.center, orbit.center)) {
    fail(`server lost the orbit ${JSON.stringify(orbit)} -> ${JSON.stringify(afterOrbit)}`);
  }
  const paintedOrbit = await waitPaintedMatches(page, afterOrbit, "orbit return");
  if (close(paintedOrbit.eye, reset.eye) && !close(reset.eye, orbit.eye)) {
    fail(`return painted the reset eye ${JSON.stringify(paintedOrbit.eye)}`);
  }

  await page.evaluate((resetEye) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const layout = JSON.parse(JSON.stringify(gd.layout));
    layout.scene.camera = Object.assign({}, layout.scene.camera, {eye: resetEye});
    return window.Plotly.react(gd, gd.data, layout);
  }, reset.eye);
  const afterStale = await waitPaintedMatches(page, afterOrbit, "stale frame");
  if (close(afterStale.eye, reset.eye) && !close(reset.eye, orbit.eye)) {
    fail(`stale frame left the reset eye painted ${JSON.stringify(afterStale.eye)}`);
  }

  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.down({button: "right"});
  await page.mouse.move(box.x + box.width * 0.5 + 70, box.y + box.height * 0.5 - 30, {steps: 8});
  await page.mouse.up({button: "right"});
  await waitForServerState(page, async (payload) => {
    const data = await (await fetch("/api/v2/state?scene_id=" + encodeURIComponent(payload.id), {cache: "no-store"})).json();
    const center = data.camera && data.camera.center;
    if (!center) return false;
    return Math.hypot(center.x - payload.center.x, center.y - payload.center.y, center.z - payload.center.z) > 0.05;
  }, {id, center: orbit.center});
  const panned = await serverCamera(page, id);
  await roundTrip(page);
  const afterPan = await serverCamera(page, id);
  if (!close(afterPan.center, panned.center)) {
    fail(`server lost the pan ${JSON.stringify(panned.center)} -> ${JSON.stringify(afterPan.center)}`);
  }
  await waitPaintedMatches(page, afterPan, "pan return");

  const zoomBox = await canvas.boundingBox();
  await page.mouse.move(zoomBox.x + zoomBox.width * 0.5, zoomBox.y + zoomBox.height * 0.5);
  await page.mouse.wheel(0, -240);
  await waitForServerState(page, async (payload) => {
    const data = await (await fetch("/api/v2/state?scene_id=" + encodeURIComponent(payload.id), {cache: "no-store"})).json();
    const eye = data.camera && data.camera.eye;
    if (!eye) return false;
    const before = Math.hypot(payload.eye.x, payload.eye.y, payload.eye.z);
    const after = Math.hypot(eye.x, eye.y, eye.z);
    return Math.abs(after - before) > 0.05;
  }, {id, eye: afterPan.eye});
  const zoomed = await serverCamera(page, id);
  await roundTrip(page);
  const afterZoom = await serverCamera(page, id);
  if (Math.abs(Math.hypot(afterZoom.eye.x, afterZoom.eye.y, afterZoom.eye.z) - Math.hypot(zoomed.eye.x, zoomed.eye.y, zoomed.eye.z)) > 0.08) {
    fail(`server lost the zoom ${JSON.stringify(zoomed.eye)} -> ${JSON.stringify(afterZoom.eye)}`);
  }
  await waitPaintedMatches(page, afterZoom, "zoom return");

  await page.locator("#view-projection input[value='orthographic']").click();
  await waitForServerState(page, async (scene) => {
    const data = await (await fetch("/api/v2/state?scene_id=" + encodeURIComponent(scene), {cache: "no-store"})).json();
    return data.projection === "orthographic";
  }, id);
  await roundTrip(page);
  const afterProjection = await serverCamera(page, id);
  if (afterProjection.projection !== "orthographic") {
    fail(`server projection reverted to ${afterProjection.projection}`);
  }
  const projected = await painted(page);
  if (!projected || !projected.ortho) {
    fail(`return painted perspective after orthographic ${JSON.stringify(projected)}`);
  }

  const posted = await page.evaluate(async (scene) => {
    const response = await fetch("/api/v2/camera?scene_id=" + encodeURIComponent(scene), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({camera: {
        eye: {x: 0.04, y: 0.02, z: 0.01},
        center: {x: 0, y: 1.6, z: 0},
        up: {x: 0, y: 0, z: 1},
        projection: {type: "orthographic"},
      }}),
    });
    return response.status;
  }, id);
  if (posted !== 200) fail(`offscreen camera post returned ${posted}`);
  await roundTrip(page);
  const refit = await serverCamera(page, id);
  if (Math.abs(Number(refit.center && refit.center.y) - 1.6) < 0.05) {
    fail(`offscreen camera survived the return ${JSON.stringify(refit)}`);
  }
  await waitPaintedMatches(page, refit, "refit return");
};
