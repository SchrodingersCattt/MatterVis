"""The server-state fence must await false observations and remain bounded."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_server_wait_awaits_false_observations_and_times_out():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the browser wait regression")
    helper = Path(__file__).with_name("browser_wait.js")
    program = r"""
const assert = require('node:assert/strict');
const wait = require(process.argv[1]);
(async () => {
  let observations = 0;
  let polls = 0;
  const page = {
    evaluate: async (predicate, arg) => predicate(arg),
    waitForTimeout: async () => { polls += 1; },
  };
  await wait(page, async wanted => {
    await Promise.resolve();
    observations += 1;
    return observations === wanted;
  }, 3);
  assert.equal(observations, 3, 'an unresolved Promise bypassed the fence');
  assert.equal(polls, 2);
  await assert.rejects(wait({
    ...page, waitForTimeout: ms => new Promise(resolve => setTimeout(resolve, ms)),
  }, async () => false, null, {timeout: 10}), /server state did not settle/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(helper)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
