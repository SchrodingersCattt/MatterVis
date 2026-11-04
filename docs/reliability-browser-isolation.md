# Browser-test isolation

Real-app browser tests drive a live Dash process. A new Playwright page does
not reset that process. Tabs, cameras, uploads, render workers, and the scene
store survive from one test to the next when they share a server.

## Rules

- Every mutating real-app browser test starts its own `ViewerBackend`,
  temporary scene directory, and HTTP listener. Module-scoped servers are not
  used for those tests.
- The listener binds port 0. The test reads the port the OS assigned. It does
  not probe for a free port and bind that number later.
- Teardown always runs, including after an assertion failure: stop the HTTP
  server, close its socket, join the server thread, then `ViewerBackend.close()`.
  A live thread, or a port that still accepts connections, fails the test.
  The leak check connects to the port. Binding it again false-fails while the
  kernel still reserves the address after a clean close.
- The restart regression is the one test that rebinds a known port. It does
  that only after the first server has shut down, and it joins both server
  threads. Werkzeug reports a busy port as `SystemExit(1)`. A failed bind
  closes that attempt's backend before the test retries.
- Readiness waits observe scene id, revision, or a Playwright response.
  `page.waitForFunction` must not call `fetch`: Playwright treats the returned
  promise as truthy before the response arrives. Server observations go through
  `tests/app/browser_wait.js`. Timeout options are the third argument
  (`waitForFunction(fn, arg, options)`), never the second.
- Two-client sessions open the second client with `browser.newContext()` and
  `context.newPage()`, and close that context in `finally`.
  `page.context().newPage()` stays in the first client's context, so it shares
  cookies, storage, and the Dash client identity. After each client has
  initialized, the test checks that the two Plotly graphs keep different scene
  ids, camera metadata, and `mattervis_render` records.
- An upload is ready only after this upload's `POST /api/v2/upload` transaction
  names a `scene_id` that was not in the scene list beforehand, the job leaves
  `pending` when it has a job id, and that id is painted on the uploading
  page's figure. A structure name already present in the tab strip is not
  readiness.
- Intentional delayed-callback tests hold and release the Dash request with
  Playwright routing. They do not sleep until a poll happens to finish. After
  the agent poll is disabled, the test waits until no
  `/_dash-update-component` request is in flight.
- A few delays are input spacing, not readiness: 40 ms between wheel notches,
  the pointer-sample gap while searching for a pickable pixel, the health
  probe while a restart has no listener, and the one-second samples in the
  mixed-projection quiescence check (six unchanged journal cursors). Those
  waits are named in the runner. Everything else observes scene id, revision,
  a Dash `changedPropIds` response, an explicit route release, or the next
  animation frame after a pointer-up whose relayout is scheduled in that event.
- On failure the Node runner writes a screenshot, a Playwright trace, console
  and network logs, and scene/camera/render JSON under `browser-artifacts/`.
  The pytest process writes the full Node stdout and stderr next to them.
  CI uploads that directory with `if: always()`. Assertions are unchanged.

`/_test/render-status` exists only on this test server. It reads worker
counters and does not mutate the scene.
