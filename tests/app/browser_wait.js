/* Await server observations before advancing browser gestures. */
module.exports = async function waitForServerState(page, predicate, arg, {timeout = 20000} = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await page.evaluate(predicate, arg)) return;
    await page.waitForTimeout(200);
  }
  throw new Error(`server state did not settle within ${timeout}ms`);
};
