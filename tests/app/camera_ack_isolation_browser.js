/* Two synthetic scenes in the real app. A's acknowledged align camera must
 * not paint on B, and returning to A must paint A's acknowledgment.
 * Component tolerance matches the roundtrip gate (0.08). Each settle waits
 * at most 20 seconds, the same bound as that gate. The first WebGL mount
 * uses the 90 second bound the other real-app scripts use.
 */
const { chromium } = require(process.argv[2]);
const chrome = process.argv[3];
const base = process.argv[4];

function fail(message) {
  throw new Error(message);
}

function close(left, right) {
  if (!left || !right) return false;
  return ["x", "y", "z"].every((name) => Math.abs(Number(left[name]) - Number(right[name])) <= 0.08);
}

async function scenes(page) {
  return page.evaluate(async () => {
    const data = await (await fetch("/api/v2/scenes", { cache: "no-store" })).json();
    return (data.scenes || []).map((scene) => ({ id: String(scene.id), label: String(scene.label || "") }));
  });
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
    const data = await (await fetch("/api/v2/state?scene_id=" + encodeURIComponent(scene), { cache: "no-store" })).json();
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
    const vec = (value) => ({ x: num(value, 0, "x"), y: num(value, 1, "y"), z: num(value, 2, "z") });
    const meta = gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    return {
      eye: vec(camera.eye),
      center: vec(camera.center),
      up: vec(camera.up),
      sceneId: meta && meta.scene_id ? String(meta.scene_id) : "",
    };
  });
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

async function clickTab(page, label, id) {
  const tab = page.locator("#scene-tabs .scene-tab-text").filter({ hasText: label }).first();
  await tab.click({ timeout: 15000 });
  await page.waitForFunction((wanted) => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
    return !!(meta && String(meta.scene_id || "") === wanted);
  }, id, { timeout: 60000 });
  await page.waitForFunction(() => {
    const gd = document.querySelector("#crystal-graph .js-plotly-plot");
    const scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    return !!(scene && scene.glplot && scene.camera);
  }, null, { timeout: 90000 });
}

async function align(page, axis) {
  await page.locator(`#view-align-${axis}`).click();
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: chrome,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--enable-webgl"],
  });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  try {
    await page.goto(base, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForFunction(() => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const scene = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
      return !!(scene && scene.glplot && scene.camera);
    }, null, { timeout: 90000 });
    const listed = await scenes(page);
    const alpha = listed.find((scene) => scene.label === "alpha_cell");
    const beta = listed.find((scene) => scene.label === "beta_cell");
    if (!alpha || !beta) fail(`synthetic scenes missing ${JSON.stringify(listed)}`);

    await clickTab(page, "alpha_cell", alpha.id);
    await page.locator("#canvas-view-reset").click();
    let reset = null;
    const resetDeadline = Date.now() + 20000;
    let resetState = null;
    while (Date.now() < resetDeadline) {
      resetState = await page.evaluate(async (scene) => {
        const response = await fetch("/api/v2/state?scene_id=" + encodeURIComponent(scene), { cache: "no-store" });
        return { status: response.status, body: await response.json() };
      }, alpha.id);
      const eye = resetState.body && resetState.body.camera && resetState.body.camera.eye;
      if (eye) {
        reset = await serverCamera(page, alpha.id);
        break;
      }
      await page.waitForTimeout(200);
    }
    if (!reset) fail(`alpha reset stored no eye ${JSON.stringify(resetState)}`);
    await waitPaintedMatches(page, reset, "alpha reset");

    await align(page, "b");
    const alphaWheelDeadline = Date.now() + 20000;
    let ackA = null;
    let alphaWheelNote = null;
    while (Date.now() < alphaWheelDeadline) {
      const server = await serverCamera(page, alpha.id);
      const live = await painted(page);
      alphaWheelNote = { server, live };
      if (server && server.eye && !close(server.eye, reset.eye)) {
        ackA = server;
        break;
      }
      await page.waitForTimeout(200);
    }
    if (!ackA) fail(`alpha align did not reach the server ${JSON.stringify(alphaWheelNote)}`);
    await waitPaintedMatches(page, ackA, "alpha acknowledgment");

    await clickTab(page, "beta_cell", beta.id);
    if ((await sceneId(page)) !== beta.id) fail("beta tab did not become the mounted scene");
    const betaOnArrival = await serverCamera(page, beta.id);
    if (betaOnArrival.eye && close(betaOnArrival.eye, ackA.eye) && close(betaOnArrival.center, ackA.center)) {
      fail(`scene B server camera is scene A's acknowledgment ${JSON.stringify(betaOnArrival)}`);
    }
    const paintedOnB = await painted(page);
    if (!paintedOnB) fail("scene B did not paint a camera");
    if (close(paintedOnB.eye, ackA.eye) && close(paintedOnB.center, ackA.center)) {
      fail(`scene A's acknowledged camera was written onto scene B ${JSON.stringify(paintedOnB)}`);
    }

    await align(page, "c");
    const betaDeadline = Date.now() + 20000;
    let ackB = null;
    let betaNote = null;
    while (Date.now() < betaDeadline) {
      const server = await serverCamera(page, beta.id);
      betaNote = server;
      if (server && server.eye && !close(server.eye, ackA.eye) && !close(server.eye, paintedOnB.eye)) {
        ackB = server;
        break;
      }
      await page.waitForTimeout(200);
    }
    if (!ackB) fail(`beta align did not reach the server ${JSON.stringify(betaNote)}`);
    if (close(ackB.eye, ackA.eye) && close(ackB.center, ackA.center)) {
      fail(`scene B's camera command stored scene A's eye ${JSON.stringify(ackB)}`);
    }
    await waitPaintedMatches(page, ackB, "beta acknowledgment");

    await page.evaluate(async () => {
      const gd = document.querySelector("#crystal-graph .js-plotly-plot");
      const layout = JSON.parse(JSON.stringify(gd.layout));
      await window.Plotly.react(gd, gd.data, layout);
    });
    const replayed = await waitPaintedMatches(page, ackB, "beta replay");
    if (close(replayed.eye, ackA.eye) && close(replayed.center, ackA.center) && !close(ackA.eye, ackB.eye)) {
      fail(`replaying scene B painted scene A's acknowledgment ${JSON.stringify(replayed)}`);
    }
    const betaAfterReplay = await serverCamera(page, beta.id);
    if (!close(betaAfterReplay.eye, ackB.eye) || !close(betaAfterReplay.center, ackB.center)) {
      fail(`scene B's server camera changed during the replay ${JSON.stringify(betaAfterReplay)}`);
    }

    await clickTab(page, "alpha_cell", alpha.id);
    const returned = await waitPaintedMatches(page, ackA, "return to alpha");
    if (close(returned.eye, ackB.eye) && !close(ackA.eye, ackB.eye)) {
      fail(`returning to scene A painted scene B ${JSON.stringify(returned)}`);
    }
    const alphaAfter = await serverCamera(page, alpha.id);
    if (!close(alphaAfter.eye, ackA.eye) || !close(alphaAfter.center, ackA.center)) {
      fail(`scene A's acknowledgment was replaced ${JSON.stringify(ackA)} -> ${JSON.stringify(alphaAfter)}`);
    }
    const betaAfter = await serverCamera(page, beta.id);
    if (!close(betaAfter.eye, ackB.eye) || !close(betaAfter.center, ackB.center)) {
      fail(`scene B's acknowledgment was replaced ${JSON.stringify(ackB)} -> ${JSON.stringify(betaAfter)}`);
    }
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
