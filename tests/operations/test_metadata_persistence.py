"""Scientific metadata survives the internal session file."""

from __future__ import annotations

import numpy as np

from effect_builders import f1_ordered_nonorthogonal
from mat_viewer.effects import EffectSession
from mat_viewer.effects.errors import PersistenceError, ValidationError
from mat_viewer.effects.persistence import load_session, save_session


def test_metadata_formula_and_image_shift_round_trip(tmp_path):
    crystal = f1_ordered_nonorthogonal()
    crystal.metadata["note"] = "keep-me"
    crystal.formula_moiety = "CO"
    session = EffectSession()
    ref = session.register(crystal, structure_id="cell")
    session.operate(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
    )
    current = session.current_ref("cell")
    original = session.checkout(current)
    assert original.metadata["note"] == "keep-me"
    assert "supercell" in original.metadata
    path = tmp_path / "session.json"
    save_session(session, str(path))
    restored = load_session(str(path))
    revived = restored.checkout(restored.current_ref("cell"))
    assert revived.metadata == original.metadata
    assert restored.fingerprint(restored.current_ref("cell")) == session.fingerprint(current)
    imported = restored.checkout(restored.revisions("cell")[0])
    assert imported.formula_moiety == "CO"
    assert imported.metadata["note"] == "keep-me"
    original_shift = np.asarray(original.molecules[0].arrays["image_shift"])
    revived_shift = np.asarray(revived.molecules[0].arrays["image_shift"])
    assert revived_shift.shape == original_shift.shape
    assert np.array_equal(revived_shift, original_shift)


def test_unrepresentable_metadata_and_site_arrays_are_rejected(tmp_path):
    opaque = f1_ordered_nonorthogonal()
    opaque.metadata["bad"] = object()
    session = EffectSession()
    try:
        session.register(opaque, structure_id="opaque")
    except ValidationError as exc:
        assert "metadata" in str(exc)
    else:
        raise AssertionError("non-serializable metadata was snapshotted")

    custom = f1_ordered_nonorthogonal()
    custom.molecules[0].set_array("custom_spin", np.array([0.2, -0.2]))
    holder = EffectSession()
    holder.register(custom, structure_id="custom")
    try:
        save_session(holder, str(tmp_path / "custom.json"))
    except PersistenceError as exc:
        assert "custom_spin" in str(exc)
    else:
        raise AssertionError("unknown site array was dropped")
