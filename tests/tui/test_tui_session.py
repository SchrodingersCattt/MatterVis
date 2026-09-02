from __future__ import annotations

from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from mat_viewer.tui.crystal_ir import AtomIR, BondIR, CrystalIR, Lattice
from mat_viewer.tui.session import (
    ACTION_SCHEMA,
    TerminalAction,
    TerminalSession,
    run_jsonl_session,
)


def _crystal() -> CrystalIR:
    return CrystalIR(
        title="session",
        lattice=Lattice(
            a=8.0,
            b=8.0,
            c=8.0,
            alpha=90.0,
            beta=90.0,
            gamma=90.0,
            matrix=np.diag([8.0, 8.0, 8.0]),
        ),
        atoms=[
            AtomIR(
                "C",
                np.array([-1.0, 0.0, 0.0]),
                np.zeros(3),
                atom_id="site:C1",
                label="C1",
                index=0,
                display_copy_id="copy:C1",
            ),
            AtomIR(
                "O",
                np.array([1.0, 0.0, 0.0]),
                np.zeros(3),
                atom_id="site:O1",
                label="O1",
                index=1,
                display_copy_id="copy:O1",
            ),
        ],
        bonds=[BondIR(0, 1, 2.0)],
    )


def test_agent_selected_actions_update_one_persistent_session() -> None:
    session = TerminalSession(
        _crystal(), width=40, height=12, mono=True, charset="ascii7"
    )
    initial = session.observe()
    rotated = session.execute(
        TerminalAction("orbit", {"yaw_deg": 45.0, "pitch_deg": 15.0})
    )
    selected = session.execute(TerminalAction("select", {"atom_id": "site:C1"}))

    assert rotated is not None and rotated.state_fingerprint != initial.state_fingerprint
    assert selected is not None
    assert selected.observation.state.selection.atom_id == "site:C1"
    assert "[C1]" in selected.observation.frame


def test_ascii7_observation_is_fixed_size_and_hashed() -> None:
    observation = TerminalSession(
        _crystal(), width=40, height=12, mono=True, charset="ascii7"
    ).observe()

    assert len(observation.screen_lines) == 12
    assert all(len(line) == 40 and line.isascii() for line in observation.screen_lines)
    screen = "\n".join(observation.screen_lines).encode("ascii")
    assert observation.screen_hash == sha256(screen).hexdigest()
    assert set("".join(observation.screen_lines)) <= set(
        " -|/\\+.*#[]ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:_"
    )


def test_full_reset_restores_initial_fingerprints() -> None:
    session = TerminalSession(
        _crystal(), width=40, height=12, mono=True, charset="ascii7"
    )
    initial = session.observe()
    session.execute(TerminalAction("orbit", {"yaw_deg": 90.0}))
    session.execute(TerminalAction("select", {"atom_id": "site:O1"}))

    reset = session.reset()

    assert reset.screen_hash == initial.screen_hash
    assert reset.state_fingerprint == initial.state_fingerprint
    assert reset.observation.state.selection.atom_id is None


def test_jsonl_transport_returns_one_response_per_action_and_continues_after_error() -> None:
    requests = [
        {"schema": ACTION_SCHEMA, "action": "observe"},
        {"schema": ACTION_SCHEMA, "action": "rotate"},
        {
            "schema": ACTION_SCHEMA,
            "action": "select",
            "arguments": {"atom_id": "site:C1"},
        },
        {"schema": ACTION_SCHEMA, "action": "close"},
    ]
    source = StringIO("".join(json.dumps(request) + "\n" for request in requests))
    target = StringIO()
    session = TerminalSession(
        _crystal(), width=40, height=12, mono=True, charset="ascii7"
    )

    run_jsonl_session(session, input_stream=source, output_stream=target)

    responses = [json.loads(line) for line in target.getvalue().splitlines()]
    assert [response["ok"] for response in responses] == [True, False, True, True]
    assert responses[1]["error"]["type"] == "ValueError"
    assert responses[2]["observation"]["selection"]["atom_id"] == "site:C1"
    assert responses[3]["closed"] is True
    with pytest.raises(RuntimeError, match="closed"):
        session.observe()


def test_action_rejects_unknown_arguments_without_mutating_state() -> None:
    session = TerminalSession(_crystal(), width=40, height=12)
    before = session.observe()

    with pytest.raises(ValueError, match="unknown arguments"):
        session.execute(TerminalAction("orbit", {"degrees": 45}))

    assert session.observe().state_fingerprint == before.state_fingerprint


def test_session_rejects_color_toggle_and_remains_usable() -> None:
    session = TerminalSession(_crystal(), width=40, height=12)

    with pytest.raises(ValueError, match="unknown arguments: mono"):
        session.execute(TerminalAction("set_display", {"mono": False}))

    observation = session.observe()
    assert all("\x1b" not in line for line in observation.screen_lines)


def test_state_fingerprint_excludes_structure_coordinates() -> None:
    session = TerminalSession(_crystal(), width=40, height=12)
    first_observation = session.observe()
    session.crystal.atoms[0].cart = (
        session.crystal.atoms[0].cart + np.array([0.25, 0.0, 0.0])
    )
    moved_observation = session.observe()

    assert first_observation.state_fingerprint == moved_observation.state_fingerprint


def test_jsonl_cli_keeps_one_live_session() -> None:
    root = Path(__file__).resolve().parents[2]
    requests = "\n".join(
        json.dumps(request)
        for request in (
            {"action": "observe"},
            {"action": "orbit", "arguments": {"yaw_deg": 30}},
            {"action": "reset"},
            {"action": "close"},
        )
    ) + "\n"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mat_viewer",
            "tui",
            "scripts/data/DAP-4.cif",
            "--session-format",
            "jsonl",
            "--charset",
            "ascii7",
            "--width",
            "40",
            "--height",
            "12",
        ],
        cwd=root,
        input=requests,
        text=True,
        capture_output=True,
        check=True,
    )

    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert len(responses) == 4
    initial = responses[0]["observation"]
    rotated = responses[1]["observation"]
    reset = responses[2]["observation"]
    assert rotated["state_fingerprint"] != initial["state_fingerprint"]
    assert reset["state_fingerprint"] == initial["state_fingerprint"]
    assert reset["frame"]["sha256"] == initial["frame"]["sha256"]
    assert responses[3]["closed"] is True


def test_default_unicode_jsonl_is_fixed_width_and_ansi_free() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mat_viewer",
            "tui",
            "scripts/data/DAP-4.cif",
            "--session-format",
            "jsonl",
            "--width",
            "40",
            "--height",
            "12",
        ],
        cwd=root,
        input='{"action":"observe"}\n{"action":"close"}\n',
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.stdout.isascii()
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    observation = responses[0]["observation"]
    assert responses[0]["ok"] is True
    assert len(observation["frame"]["lines"]) == 12
    assert all(len(line) == 40 for line in observation["frame"]["lines"])
    assert all("\x1b" not in line for line in observation["frame"]["lines"])
