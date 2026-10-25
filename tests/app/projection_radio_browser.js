/* Real Dash radio delivery under scene switches and delayed store callbacks. */
module.exports = async function projectionRadio(page, clickTab, waitForServerState) {
  const states = await page.evaluate(async () => {
    const scenes = await fetch('/api/v2/scenes').then(response => response.json());
    const wanted = ['DAP-4', 'ionic_nacl'].map(name => scenes.scenes.find(scene =>
      String(scene.structure_name).includes(name)));
    if (wanted.some(scene => !scene)) throw new Error('projection radio scenes are missing');
    return Promise.all(wanted.map(scene => fetch('/api/v2/state?scene_id=' +
      encodeURIComponent(scene.id)).then(response => response.json())));
  });
  const [sceneA, sceneB] = states;
  const expected = ['orthographic', 'perspective'];
  const observations = [];
  const errors = [];
  const recordError = error => errors.push(String(error && error.message || error));
  page.on('pageerror', recordError);
  const isSync = request => {
    if (new URL(request.url()).pathname !== '/_dash-update-component') return false;
    const payload = request.postDataJSON();
    return String(payload.output).startsWith('view-projection.value@')
      && payload.inputs.some(input => input.id === 'agent-state-store')
      && payload.inputs.some(input => input.id === 'scene-tabs');
  };
  async function waitRadio(scene, projection) {
    await waitForServerState(page, async ({id, projection}) => {
      const state = await fetch('/api/v2/state?scene_id=' + encodeURIComponent(id))
        .then(response => response.json());
      const radio = document.querySelector('#view-projection input:checked');
      return !!radio && radio.value === projection && state.projection === projection;
    }, {id: scene.scene_id, projection});
  }
  async function injectStore(state, token) {
    const response = page.waitForResponse(response => {
      if (!isSync(response.request())) return false;
      const payload = response.request().postDataJSON();
      return payload.inputs.some(input => input.id === 'agent-state-store'
        && input.value && input.value.projection_radio_test === token);
    });
    await page.evaluate(value => window.dash_clientside.set_props(
      'agent-state-store', {data: value}), {...state, projection_radio_test: token});
    const delivered = await response;
    const body = delivered.status() === 204 ? {} : await delivered.json();
    observations.push({token, status: delivered.status(), response: body.response || {}});
    return body.response || {};
  }
  async function holdStoreResponse(state, token) {
    let captured, release, fulfilled, capturedRequest;
    const held = new Promise(resolve => { captured = resolve; });
    const gate = new Promise(resolve => { release = resolve; });
    const delivered = new Promise(resolve => { fulfilled = resolve; });
    const handler = async route => {
      const request = route.request();
      if (capturedRequest || !isSync(request) || !request.postDataJSON().inputs.some(input =>
        input.id === 'agent-state-store' && input.value
        && input.value.projection_radio_test === token)) {
        await route.continue();
        return;
      }
      capturedRequest = request;
      const response = await route.fetch();
      captured();
      await gate;
      await route.fulfill({response});
      fulfilled();
    };
    await page.route('**/_dash-update-component*', handler);
    await page.evaluate(value => window.dash_clientside.set_props('agent-state-store', {
      data: value,
    }), {...state, projection_radio_test: token});
    await Promise.race([held, page.waitForTimeout(20000).then(() => {
      throw new Error('did not capture delayed projection radio callback: ' + token);
    })]);
    return async () => {
      const response = page.waitForResponse(response => response.request() === capturedRequest);
      release();
      await delivered;
      await response;
      await page.unroute('**/_dash-update-component*', handler);
      await page.waitForTimeout(300);
    };
  }
  try {
    await page.evaluate(async ({states, expected}) => {
      for (let index = 0; index < states.length; index += 1) {
        const response = await fetch('/api/v2/state', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({scene_id: states[index].scene_id, projection: expected[index]}),
        });
        if (!response.ok) throw new Error('could not seed projection radio scene');
      }
    }, {states, expected});
    await clickTab(page, 'DAP-4');
    await waitRadio(sceneA, expected[0]);
    await page.locator("#view-projection input[value='perspective']").check();
    await waitRadio(sceneA, 'perspective');
    await page.locator("#view-projection input[value='orthographic']").check();
    await waitRadio(sceneA, 'orthographic');
    await clickTab(page, 'ionic_nacl');
    await waitRadio(sceneB, expected[1]);

    const sameScene = await injectStore({...sceneB, projection: 'orthographic',
      camera_revision: 0, render_revision: 0}, 'stale-same-scene');
    if (!sameScene['view-projection'] || sameScene['view-projection'].value !== 'perspective') {
      throw new Error('stale same-scene store did not read live projection: ' + JSON.stringify(sameScene));
    }
    await waitRadio(sceneB, 'perspective');
    const wrongScene = await injectStore({...sceneA, projection: 'orthographic'}, 'wrong-scene');
    if (wrongScene['view-projection']) {
      throw new Error('another scene wrote the selected radio: ' + JSON.stringify(wrongScene));
    }
    await waitRadio(sceneB, 'perspective');

    // Hold a same-scene read from before a native click. Polling is paused so
    // a later poll cannot mask delivery of that older response.
    await page.evaluate(() => window.dash_clientside.set_props('agent-state-poll', {disabled: true}));
    const releaseBeforeClick = await holdStoreResponse({...sceneB, projection: 'perspective'},
      'delayed-before-native-click');
    try {
      await page.locator("#view-projection input[value='orthographic']").check();
      await waitRadio(sceneB, 'orthographic');
      await releaseBeforeClick();
      await waitRadio(sceneB, 'orthographic');
    } finally {
      await page.evaluate(() => window.dash_clientside.set_props('agent-state-poll', {disabled: false}));
    }
    await page.locator("#view-projection input[value='perspective']").check();
    await waitRadio(sceneB, 'perspective');

    await clickTab(page, 'DAP-4');
    await waitRadio(sceneA, 'orthographic');
    const releaseOldScene = await holdStoreResponse({...sceneA, projection: 'orthographic'},
      'delayed-old-scene');
    await clickTab(page, 'ionic_nacl');
    await waitRadio(sceneB, 'perspective');
    await releaseOldScene();
    await waitRadio(sceneB, 'perspective');
    const projectionA = await page.evaluate(async id =>
      (await fetch('/api/v2/state?scene_id=' + encodeURIComponent(id))
        .then(response => response.json())).projection, sceneA.scene_id);
    if (projectionA !== 'orthographic') throw new Error('scene switch rewrote the old scene projection');
    console.log('projection-radio observations ' + JSON.stringify(observations));
    if (errors.length) throw new Error('projection radio page errors: ' + errors.join('; '));
  } finally {
    page.off('pageerror', recordError);
    await page.evaluate(async states => {
      for (const state of states) {
        await fetch('/api/v2/state', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({scene_id: state.scene_id, projection: state.projection, camera: state.camera})});
      }
    }, states);
  }
};
