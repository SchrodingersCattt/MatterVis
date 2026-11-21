"""A wedged xdist worker must name the test and exit.

The Python 3.12 full job stayed at 98% for six hours after one worker
reported ``Not properly terminated``. Replacing that worker left the
controller waiting. These children assert the two bounds: a timeout is
one failed test, and a hang after the test item still ends the process.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "tests"


def _run(
    tmp_path: Path,
    body: str,
    *,
    extra_env: dict[str, str],
    timeout: float,
    per_test_timeout: str = "1",
) -> subprocess.CompletedProcess[str]:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_guard_sample.py").write_text(textwrap.dedent(body), encoding="utf-8")
    (suite / "pytest.ini").write_text(
        textwrap.dedent(
            f"""
            [pytest]
            timeout = {per_test_timeout}
            timeout_method = thread
            filterwarnings =
                ignore::pytest.PytestRemovedIn10Warning
            """
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(GUARD) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env)
    started = time.monotonic()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "-n",
            "2",
            "--dist",
            "loadfile",
            "-p",
            "_pytest_worker_guard",
            "-p",
            "no:cacheprovider",
            "--noconftest",
            "-q",
            str(suite / "test_guard_sample.py"),
        ],
        cwd=suite,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        raise
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elapsed = time.monotonic() - started
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err + f"\nELAPSED {elapsed:.2f}\n")


def test_timeout_names_the_test_and_does_not_replace_the_worker(tmp_path: Path):
    completed = _run(
        tmp_path,
        """
        import time

        def test_passes():
            assert True

        def test_sleeps_past_the_timeout():
            time.sleep(30)
        """,
        extra_env={},
        timeout=20,
    )
    report = completed.stdout + completed.stderr
    assert "test_sleeps_past_the_timeout" in report
    assert "replacing crashed worker" not in report
    assert completed.returncode != 0
    elapsed = float(report.rsplit("ELAPSED ", 1)[1].split()[0])
    assert elapsed < 15


def test_slow_test_inside_its_timeout_is_not_a_stall(tmp_path: Path):
    """A passing test longer than the silence bound must not fail the run.

    The state file is removed when the test ends. Silence is measured from
    the last moment that file was still inside its timeout, not from the
    moment the test started.
    """
    completed = _run(
        tmp_path,
        """
        import time

        def test_runs_longer_than_silence():
            time.sleep(6)
        """,
        extra_env={
            "MATTERVIS_PYTEST_STALL_SILENCE": "2",
            "MATTERVIS_PYTEST_STALL_COLLECTION": "30",
            "MATTERVIS_PYTEST_STALL_GRACE": "30",
        },
        timeout=20,
        per_test_timeout="30",
    )
    report = completed.stdout + completed.stderr
    assert "pytest stalled" not in report
    assert completed.returncode == 0, report
    elapsed = float(report.rsplit("ELAPSED ", 1)[1].split()[0])
    assert elapsed < 18


def test_controller_exits_when_reports_stop_inside_a_test(tmp_path: Path):
    """A test with no timer still ends the controller.

    ``timeout = 0`` leaves the per-test timer unarmed, which is the same
    shape as a hang after the timer has already been cancelled. The
    controller's silence bound is what ends that process.
    """
    completed = _run(
        tmp_path,
        """
        import time

        def test_blocks_without_a_timer():
            time.sleep(300)
        """,
        extra_env={
            "MATTERVIS_PYTEST_STALL_SILENCE": "3",
            "MATTERVIS_PYTEST_STALL_COLLECTION": "30",
            "MATTERVIS_PYTEST_STALL_GRACE": "2",
        },
        timeout=25,
        per_test_timeout="0",
    )
    report = completed.stdout + completed.stderr
    assert "pytest stalled" in report
    assert "test_blocks_without_a_timer" in report
    assert completed.returncode != 0
    elapsed = float(report.rsplit("ELAPSED ", 1)[1].split()[0])
    assert elapsed < 20
