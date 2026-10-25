"""Browser gate for Plotly.gl3d camera projection and visible geometry.

The fast Node tests cover ordering and failure semantics.  This test runs the
actual Plotly WebGL scene when Chromium, Playwright, and a local Plotly bundle
are available.  The Camera WebGL CI job sets ``MATTERVIS_REQUIRE_BROWSER=1``
and fails when those tools are missing.  Other CI jobs exclude this marker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser


def _plotly_bundle() -> Path | None:
    candidates = [
        Path("/usr/share/python3-plotly/plotly.min.js"),
    ]
    try:
        import plotly
    except ImportError:
        plotly = None
    if plotly is not None:
        candidates.insert(0, Path(plotly.__file__).resolve().parent / "package_data" / "plotly.min.js")
    for version in ("3.10", "3.11", "3.12", "3.13"):
        candidates.append(Path(sys.prefix) / "lib" / f"python{version}" / "site-packages" / "plotly" / "package_data" / "plotly.min.js")
    return next((path for path in candidates if path.is_file()), None)


def _unavailable(reason: str) -> None:
    if os.environ.get("MATTERVIS_REQUIRE_BROWSER") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def _chrome() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _playwright_module(node: str) -> str | None:
    for package in ("playwright", "playwright-core"):
        try:
            return subprocess.check_output(
                [node, "-e", f"process.stdout.write(require.resolve({package!r}))"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def test_real_plotly_webgl_projection_and_reset():
    node = shutil.which("node")
    chromium = _chrome()
    bundle = _plotly_bundle()
    playwright = _playwright_module(node) if node else None
    if not node or not chromium or bundle is None or not playwright:
        _unavailable(
            "visual browser gate unavailable: requires node, Chrome/Chromium, Playwright, and a local plotly.min.js bundle"
        )

    root = Path(__file__).resolve().parents[2] / "frontend" / "assets"
    camera_source = root / "view_camera.js"
    updates_source = root / "view_updates.js"
    program = r"""
const zlib = require('node:zlib');
const {chromium} = require(process.argv[1]);
const plotlyPath = process.argv[2], cameraPath = process.argv[3], updatesPath = process.argv[4], chromiumPath = process.argv[5];
function decodePng(buffer) {
  const png = Buffer.from(buffer);
  let offset = 8, width = 0, height = 0, color = 6, idat = [];
  while (offset < png.length) {
    const length = png.readUInt32BE(offset);
    const type = png.toString('ascii', offset + 4, offset + 8);
    const data = png.subarray(offset + 8, offset + 8 + length);
    if (type === 'IHDR') { width = data.readUInt32BE(0); height = data.readUInt32BE(4); color = data[9]; }
    else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
    offset += 12 + length;
  }
  const inflated = zlib.inflateSync(Buffer.concat(idat));
  const channels = {0:1, 2:3, 3:1, 4:2, 6:4}[color];
  const stride = width * channels;
  const raw = Buffer.alloc(height * stride);
  let src = 0;
  for (let y = 0; y < height; y++) {
    const filter = inflated[src++];
    const row = inflated.subarray(src, src + stride);
    src += stride;
    const prev = y ? raw.subarray((y - 1) * stride, y * stride) : Buffer.alloc(stride);
    const dest = raw.subarray(y * stride, (y + 1) * stride);
    for (let i = 0; i < stride; i++) {
      const left = i >= channels ? dest[i - channels] : 0;
      const up = prev[i];
      const ul = i >= channels ? prev[i - channels] : 0;
      let pred = 0;
      if (filter === 1) pred = left;
      else if (filter === 2) pred = up;
      else if (filter === 3) pred = Math.floor((left + up) / 2);
      else if (filter === 4) {
        const p = left + up - ul;
        const pa = Math.abs(p - left), pb = Math.abs(p - up), pc = Math.abs(p - ul);
        pred = pa <= pb && pa <= pc ? left : pb <= pc ? up : ul;
      }
      dest[i] = (row[i] + pred) & 255;
    }
  }
  return {width, height, raw, channels};
}
function nonBackground(buffer) {
  const {width, height, raw, channels} = decodePng(buffer);
  const bg = [raw[0], raw[1] || 0, raw[2] || 0];
  let count = 0;
  for (let i = 0; i < width * height; i++) {
    const at = i * channels;
    const delta = Math.abs(raw[at] - bg[0]) + Math.abs((raw[at + 1] || 0) - bg[1]) + Math.abs((raw[at + 2] || 0) - bg[2]);
    if (delta > 48) count += 1;
  }
  return count;
}
let browser;
(async () => {
  try {
    browser = await chromium.launch({headless:true, executablePath:chromiumPath,
      args:['--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader','--enable-webgl']});
  } catch (error) {
    console.error('BROWSER_GATE_UNAVAILABLE: ' + error.message);
    process.exitCode = 2;
    return;
  }
  const page = await browser.newPage({viewport:{width:720,height:540}});
  await page.setContent('<div id="crystal-graph"><div class="js-plotly-plot" style="width:720px;height:540px"></div></div>');
  await page.addScriptTag({path:plotlyPath});
  await page.addScriptTag({path:cameraPath});
  await page.addScriptTag({path:updatesPath});
  await page.evaluate(async () => {
    const gd = document.querySelector('.js-plotly-plot');
    const camera = {eye:{x:1.5,y:1.5,z:1.5}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'perspective'}};
    await Plotly.newPlot(gd, [{type:'scatter3d', mode:'markers', x:[0,1,0], y:[0,0,1], z:[0,0,0], marker:{size:18, color:'#d62728'}}], {scene:{camera, bgcolor:'#ffffff'}});
    gd.layout.meta = {mattervis_render:{scene_id:'browser', scene_generation:1, geometry_version:1, display_version:1, camera_version:0}};
  });
  const samples = [];
  let version = 0;
  const commands = [
    {type:'orthographic', command:''},
    {type:'perspective', command:''},
    {type:'orthographic', command:''},
    {type:'orthographic', command:'reset'},
  ];
  for (const {type, command} of commands) {
    version += 1;
    const outcome = await page.evaluate(async ({type, command, version}) => {
      const gd = document.querySelector('.js-plotly-plot');
      const result = await MatterVisViewUpdates.submit({
        type:'view_update', update_kind:'camera', scene_id:'browser', scene_generation:1,
        geometry_version:1, display_version:1, camera_version:version,
        payload:{command, camera:{eye:{x:1.5,y:1.5,z:1.5}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type}}},
      });
      const actual = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene && gd._fullLayout.scene._scene.getCamera();
      const layoutCamera = gd.layout && gd.layout.scene && gd.layout.scene.camera;
      return {
        result,
        projection: actual && actual.projection && actual.projection.type,
        layoutType: layoutCamera && layoutCamera.projection && layoutCamera.projection.type,
        command,
        visible: MatterVisViewCamera.visibleGeometry(gd),
      };
    }, {type, command, version});
    await page.waitForTimeout(150);
    const clip = await page.evaluate(() => {
      const canvases = Array.from(document.querySelectorAll('canvas'));
      const canvas = canvases.sort((a, b) => (b.width * b.height) - (a.width * a.height))[0];
      const rect = canvas.getBoundingClientRect();
      return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
    });
    const pixels = nonBackground(await page.screenshot({clip}));
    samples.push(Object.assign({requested:type, pixels}, outcome));
  }
  if (samples.some(sample => sample.result !== 'camera' || !sample.visible || sample.projection !== sample.requested || sample.layoutType !== sample.requested || sample.pixels === 0)) {
    throw new Error('Plotly WebGL camera/geometry postcondition failed: ' + JSON.stringify(samples));
  }
  // Exercise the production full-frame gate with the actual GL camera too.
  const fs = require('node:fs'), path = require('node:path');
  const gate = fs.readFileSync(path.join(path.dirname(cameraPath), 'mattervis.js'), 'utf8');
  const end = gate.indexOf('  // ── Right-click menu');
  const refreshStart = gate.indexOf('  function refreshSettledGestureStamp(');
  const refreshEnd = gate.indexOf('  function stampGestureScene(', refreshStart);
  const cameraStart = gate.indexOf('  function displayedSceneCamera(');
  const cameraEnd = gate.indexOf('  function redrawCompass(', cameraStart);
  await page.evaluate(() => {
    const gd = document.querySelector('.js-plotly-plot');
    const meta = {scene_id:'browser', scene_generation:1, render_revision:1, geometry_version:1, display_version:1, camera_revision:4, server_started_at:'epoch'};
    gd.layout.meta = {mattervis_render:meta};
    const node = document.createElement('div');
    node.id = 'fast-view-metadata'; node.textContent = JSON.stringify(meta); document.body.appendChild(node);
    const react = Plotly.react;
    Plotly.react = function (...args) {
      if (window.holdNextReact) {
        window.holdNextReact = false;
        return new Promise((resolve, reject) => {
          window.releaseReact = () => Promise.resolve(react.apply(this, args)).then(resolve, reject);
        });
      }
      return react.apply(this, args);
    };
  });
  await page.addScriptTag({content:gate.slice(0,end) + gate.slice(refreshStart,refreshEnd) + '})();' + gate.slice(cameraStart,cameraEnd)});
  const frameSamples = await page.evaluate(async () => {
    const gd = document.querySelector('.js-plotly-plot');
    const copy = value => JSON.parse(JSON.stringify(value));
    const camera = {eye:{x:1.5,y:1.5,z:1.5},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1},projection:{type:'orthographic'}};
    const stale = copy(gd.layout);
    stale.scene.camera = copy(camera); stale.scene.camera.projection.type = 'perspective';
    const result = await MatterVisViewUpdates.submit({type:'view_update', update_kind:'camera',scene_id:'browser',scene_generation:1,
      geometry_version:1,display_version:1,camera_version:5,payload:{camera}});
    if (result !== 'camera') throw new Error('camera command was rejected: ' + result);
    const draggedEye = {x:2,y:1,z:1.5};
    await Plotly.relayout(gd, {'scene.camera.eye':draggedEye});
    await Plotly.react(gd, gd.data, stale);
    const afterStale = copy(gd._fullLayout.scene._scene.getCamera());
    const current = copy(gd.layout);
    current.meta.mattervis_render.render_revision = 2;
    current.meta.mattervis_render.camera_revision = 5;
    current.scene.camera = copy(camera); current.scene.camera.projection.type = 'perspective';
    await Plotly.react(gd, {data:gd.data,layout:current});
    return {afterStale, draggedEye};
  });
  await page.waitForFunction(() => document.querySelector('.js-plotly-plot')._fullLayout.scene._scene.getCamera().projection.type === 'perspective');
  const afterFrame = await page.evaluate(() => window.mattervisCurrentCamera(document.querySelector('.js-plotly-plot')));
  for (const sample of [frameSamples.afterStale, afterFrame]) {
    if (['x','y','z'].some(axis => Math.abs(sample.eye[axis] - frameSamples.draggedEye[axis]) > 0.05)) {
      throw new Error('Full frame replaced dragged GL eye: ' + JSON.stringify({frameSamples,afterFrame}));
    }
  }
  if (frameSamples.afterStale.projection.type !== 'orthographic') {
    throw new Error('Stale full frame undid newer GL projection: ' + JSON.stringify(frameSamples));
  }
  const afterLateFrame = await page.evaluate(async () => {
    const gd = document.querySelector('.js-plotly-plot');
    const layout = JSON.parse(JSON.stringify(gd.layout));
    layout.meta.mattervis_render.render_revision = 3;
    window.holdNextReact = true;
    const running = Plotly.react(gd, gd.data, layout);
    for (let i = 0; i < 40 && !window.releaseReact; i++) await Promise.resolve();
    if (!window.releaseReact) throw new Error('held full frame never started');
    const camera = {eye:{x:1.5,y:1.5,z:1.5},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1},projection:{type:'orthographic'}};
    const result = await MatterVisViewUpdates.submit({type:'view_update',update_kind:'camera',scene_id:'browser',scene_generation:1,
      geometry_version:1,display_version:1,camera_version:6,payload:{camera}});
    if (result !== 'camera') throw new Error('newer camera command was rejected: ' + result);
    await window.releaseReact();
    await running;
    return gd._fullLayout.scene._scene.getCamera();
  });
  if (afterLateFrame.projection.type !== 'orthographic' ||
      ['x','y','z'].some(axis => Math.abs(afterLateFrame.eye[axis] - 1.5) > 0.05)) {
    throw new Error('Late GL frame undid newer camera: ' + JSON.stringify(afterLateFrame));
  }
  const screenshot = await page.screenshot(process.env.MATTERVIS_CAMERA_SCREENSHOT ? {path:process.env.MATTERVIS_CAMERA_SCREENSHOT} : {});
  if (nonBackground(screenshot) === 0) throw new Error('Full-frame projection left a blank canvas');
  await browser.close();
})().catch(async error => { console.error(error); if (browser) await browser.close(); process.exitCode = process.exitCode || 1; });
"""
    result = subprocess.run(
        [node, "-e", program, playwright, str(bundle), str(camera_source), str(updates_source), chromium],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    if result.returncode == 2 and "BROWSER_GATE_UNAVAILABLE:" in result.stderr:
        _unavailable(result.stderr.strip())
    assert result.returncode == 0, result.stderr
