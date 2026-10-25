/* A mixed REST transaction must deliver camera and display edits together. */
module.exports = async function projectionMixed(page, clickTab, waitForServerState) {
  await clickTab(page, 'ionic_nacl');
  async function snapshot() {
    return page.evaluate(async () => {
      const gd = document.querySelector('#crystal-graph .js-plotly-plot');
      const meta = gd.layout.meta.mattervis_render;
      const state = await fetch('/api/v2/state?scene_id=' + encodeURIComponent(meta.scene_id),
        {cache: 'no-store'}).then(response => response.json());
      const controls = JSON.parse(document.getElementById('scene-controls-scene').textContent);
      const status = await fetch('/_test/render-status', {cache: 'no-store'})
        .then(response => response.json());
      const camera = gd._fullLayout.scene._scene.getCamera();
      const radio = document.querySelector('#view-projection input:checked');
      const minor = gd.data.filter(trace => trace.meta && trace.meta.mv_minor
        && ['atom', 'bond'].includes(trace.meta.mv_role));
      return {
        state, meta, controls, status,
        projection: camera.projection.type,
        radio: radio && radio.value,
        minorOpacity: minor.map(trace => Number(trace.opacity)),
      };
    });
  }
  // Establish a persisted camera through the native control before testing
  // the REST mirror; the API preserves that camera instead of fitting one.
  for (const projection of ['orthographic', 'perspective']) {
    await page.locator("#view-projection input[value='" + projection + "']").check();
    await waitForServerState(page, async projection => {
      const gd = document.querySelector('#crystal-graph .js-plotly-plot');
      const id = gd.layout.meta.mattervis_render.scene_id;
      const state = await fetch('/api/v2/state?scene_id=' + encodeURIComponent(id))
        .then(response => response.json());
      return state.projection === projection && state.camera && state.camera.eye;
    }, projection);
  }
  // Six unchanged, idle seconds cover multiple normal Dash polls and exclude
  // an earlier worker frame that could otherwise hide a missing camera event.
  let stable = 0, previousCursor = -1, before;
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline && stable < 6) {
    await page.waitForTimeout(1000);
    before = await snapshot();
    const {state, meta, controls, status} = before;
    const ready = !status.pending.render && !status.pending.topology && !status.pending_state
      && status.cursor === previousCursor
      && controls.scene_id === state.scene_id && controls.camera_revision === state.camera_revision
      && meta.scene_id === state.scene_id
      && before.projection === state.projection && before.radio === state.projection;
    stable = ready ? stable + 1 : 0;
    previousCursor = status.cursor;
  }
  if (stable < 6) throw new Error('mixed projection initial scene never settled: ' + JSON.stringify(before));
  const projection = before.projection === 'orthographic' ? 'perspective' : 'orthographic';
  const opacity = 0.31;
  const result = await page.evaluate(async ({id, projection, opacity, cursor}) => {
    const response = await fetch('/api/v2/state', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scene_id: id, projection, minor_opacity: opacity}),
    });
    const state = await response.json();
    const journal = await fetch('/api/v2/view-updates?since=' + cursor + '&scene_id=' +
      encodeURIComponent(id), {cache: 'no-store'}).then(response => response.json());
    return {status: response.status, state, updates: journal.updates};
  }, {id: before.state.scene_id, projection, opacity, cursor: before.status.cursor});
  if (result.status !== 200) throw new Error('mixed REST patch failed: ' + JSON.stringify(result));
  for (const component of ['eye', 'center', 'up']) {
    if (JSON.stringify(result.state.camera[component]) !== JSON.stringify(before.state.camera[component])) {
      throw new Error('mixed projection refit the persisted camera component: ' + component);
    }
  }
  const current = update => update.scene_id === result.state.scene_id
    && update.camera_version === result.state.camera_revision
    && update.display_version === result.state.display_version;
  const camera = result.updates.find(update => current(update)
    && (update.update_kind || update.kind) === 'camera'
    && update.payload && update.payload.camera && update.payload.camera.projection.type === projection);
  const display = result.updates.find(update => current(update)
    && (update.update_kind || update.kind) === 'display'
    && update.payload && update.payload.minor_opacity === opacity);
  if (!camera || !display) {
    throw new Error('mixed REST patch did not publish both transaction components: ' + JSON.stringify({
      requested: {projection, opacity}, updates: result.updates.map(update => ({
        kind: update.update_kind || update.kind, scene: update.scene_id,
        camera: update.camera_version, display: update.display_version, payload: update.payload,
      })),
    }));
  }
  if (!camera.operation_id || !display.operation_id || camera.operation_id === display.operation_id) {
    throw new Error('mixed transaction components share a delivery identity');
  }
  await waitForServerState(page, async ({id, projection, opacity, expectMinor}) => {
    const state = await fetch('/api/v2/state?scene_id=' + encodeURIComponent(id), {cache: 'no-store'})
      .then(response => response.json());
    const gd = document.querySelector('#crystal-graph .js-plotly-plot');
    const camera = gd._fullLayout.scene._scene.getCamera();
    const radio = document.querySelector('#view-projection input:checked');
    const minor = gd.data.filter(trace => trace.meta && trace.meta.mv_minor
      && ['atom', 'bond'].includes(trace.meta.mv_role));
    const sliderRoot = document.getElementById('minor-opacity-slider');
    const slider = sliderRoot && (sliderRoot.querySelector('[aria-valuenow]')
      || sliderRoot.querySelector('input'));
    const sliderValue = slider && Number(slider.getAttribute('aria-valuenow') || slider.value);
    return state.projection === projection && state.camera.projection.type === projection
      && state.minor_opacity === opacity && sliderValue === opacity && radio && radio.value === projection
      && camera.projection.type === projection && (!expectMinor || minor.length)
      && minor.every(trace => Math.abs(Number(trace.opacity) - opacity) < 1e-6);
  }, {id: result.state.scene_id, projection, opacity, expectMinor: before.minorOpacity.length > 0});
  console.log('mixed-projection observation ' + JSON.stringify({
    scene: result.state.scene_id, projection, opacity,
    cameraVersion: result.state.camera_revision, displayVersion: result.state.display_version,
    minorTraces: before.minorOpacity.length,
  }));
};
