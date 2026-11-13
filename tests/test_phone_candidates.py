"""Ordered disorder candidates: bounded, honest, independent of each other."""

import copy
import hashlib
import inspect
import json
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

import mat_viewer.phone as phone
from mat_viewer.phone import (
    CandidatePayloadError,
    CandidateRequestError,
    analyze_cif,
    generate_disorder_candidates,
    operate_commit_major,
    operate_commit_major_with_cif,
)

# Byte-for-byte copy of MolCrysKit tests/data/cif/synthetic_two_assemblies.cif
# (two multi-group assemblies A and B; O1 ordered).
TWO_ASSEMBLIES = """data_synthetic_two_assemblies
_chemical_formula_moiety 'H2 O'
_cell_formula_units_Z 1
_space_group_IT_number 1
_space_group_name_H-M_alt 'P 1'
_cell_length_a 12
_cell_length_b 12
_cell_length_c 12
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90

loop_
_space_group_symop_id
_space_group_symop_operation_xyz
1 x,y,z

loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
_atom_site_disorder_assembly
_atom_site_disorder_group
O1 O 0.5000 0.5000 0.5000 1.0 . .
H1 H 0.5797 0.5000 0.5000 0.6 A 1
H2 H 0.4800 0.5771 0.5000 0.6 B 1
H3 H 0.5000 0.5000 0.5797 0.4 A 2
H4 H 0.5000 0.5771 0.4800 0.4 B 2
"""

ORDERED = TWO_ASSEMBLIES.replace(" 0.6 A 1", " 1.0 . .").replace(" 0.6 B 1", " 1.0 . .")
ASSEMBLY_WARNING = "Multiple multi-group assemblies detected"


@pytest.fixture
def cif(tmp_path: Path) -> Path:
    path = tmp_path / "two.cif"
    path.write_bytes(TWO_ASSEMBLIES.encode("utf-8"))
    return path


def _gen(path, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return generate_disorder_candidates(path, **kwargs)


def _kept(result):
    return [c["kept_indices"] for c in result["candidates"]]


def _messages(result):
    return [w["message"] for w in result["warnings"]]


def _same_structure(left: dict, right: dict) -> None:
    assert np.allclose(left["cell"], right["cell"], atol=1e-4)
    key = lambda s: (s["symbol"], round(s["occupancy"], 4))  # noqa: E731
    assert sorted(map(key, left["sites"])) == sorted(map(key, right["sites"]))
    inv_l, inv_r = np.linalg.inv(left["cell"]), np.linalg.inv(right["cell"])
    for site in left["sites"]:
        frac = np.array(site["position"]) @ inv_l
        others = [np.array(o["position"]) @ inv_r for o in right["sites"] if key(o) == key(site)]
        assert others
        assert min(np.abs((frac - o) - np.round(frac - o)).max() for o in others) < 1e-4


def test_enumerate_legacy_default_keeps_assemblies_coupled_and_warns(cif):
    result = _gen(cif, method="enumerate", count=5)
    assert result["actual_count"] == 2 and result["requested_count"] == 5
    assert _kept(result) == [[0, 1, 2], [0, 3, 4]]
    assert result["independent_assemblies"] is False and result["coupled"] is False
    assert result["seed"] is None
    # MCK's own warning is preserved, plus an honest shortfall notice.
    assert any(ASSEMBLY_WARNING in m for m in _messages(result))
    assert any("requested 5" in m and "none were fabricated" in m for m in _messages(result))


def test_enumerate_independent_assemblies_is_explicit_and_larger(cif):
    result = _gen(cif, method="enumerate", count=5, independent_assemblies=True)
    assert result["independent_assemblies"] is True
    assert result["actual_count"] == 4
    assert _kept(result) == [[0, 1, 2], [0, 2, 3], [0, 1, 4], [0, 3, 4]]
    assert not any(ASSEMBLY_WARNING in m for m in _messages(result))
    # Count is a cap: never more than asked.
    assert _gen(cif, count=3, independent_assemblies=True)["actual_count"] == 3


def test_enumerate_is_deterministic_and_ordered(cif):
    first = _gen(cif, count=4, independent_assemblies=True)
    second = _gen(cif, count=4, independent_assemblies=True)
    assert first == second
    assert [c["candidate_id"] for c in first["candidates"]] == [
        "candidate-001", "candidate-002", "candidate-003", "candidate-004",
    ]
    assert [c["index"] for c in first["candidates"]] == [0, 1, 2, 3]
    # Distinct identities and distinct content.
    assert len({c["fingerprint"] for c in first["candidates"]}) == 4
    assert len({c["cif"] for c in first["candidates"]}) == 4
    assert all(c["duplicate_of"] is None for c in first["candidates"])
    assert first["unique_count"] == 4


def test_random_is_seed_reproducible_and_honest_about_duplicates(cif):
    result = _gen(cif, method="random", count=5, seed=42)
    assert result["seed"] == 42 and result["method"] == "random"
    assert result["actual_count"] == 5
    assert result == _gen(cif, method="random", count=5, seed=42)
    kept = _kept(result)
    assert kept.count([0, 1, 2]) >= 2  # occupancy-weighted sampling repeats
    assert result["unique_count"] == len({tuple(k) for k in kept}) < 5
    seen = {}
    for candidate in result["candidates"]:
        key = tuple(candidate["kept_indices"])
        assert candidate["duplicate_of"] == seen.get(key)
        seen.setdefault(key, candidate["candidate_id"])
    assert any("repeat an earlier kept_indices" in m for m in _messages(result))
    # Duplicates keep their own position and ID, and identical content.
    dup = next(c for c in result["candidates"] if c["duplicate_of"])
    first = next(c for c in result["candidates"] if c["candidate_id"] == dup["duplicate_of"])
    assert dup["candidate_id"] != first["candidate_id"] and dup["index"] > first["index"]
    assert dup["cif"] == first["cif"]


def test_coupled_flag_is_forwarded(cif):
    coupled = _gen(cif, method="random", count=5, seed=42, coupled=True)
    assert coupled["coupled"] is True
    assert coupled != _gen(cif, method="random", count=5, seed=42)


def test_source_is_never_replaced(cif):
    before_bytes = cif.read_bytes()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        before = analyze_cif(cif)
        result = generate_disorder_candidates(cif, count=4, independent_assemblies=True)
        after = analyze_cif(cif)
    assert cif.read_bytes() == before_bytes
    assert before == after and len(before["sites"]) == 5
    assert result["source"]["sha256"] == hashlib.sha256(before_bytes).hexdigest()
    assert result["source"]["bytes"] == len(before_bytes)
    assert all(len(c["snapshot"]["sites"]) == 3 for c in result["candidates"])
    # Original input snapshot is not any candidate.
    assert all(c["snapshot"]["sites"] != before["sites"] for c in result["candidates"])


def test_snapshot_and_cif_are_the_same_candidate(cif, tmp_path: Path):
    result = _gen(cif, count=4, independent_assemblies=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        source = analyze_cif(cif)
    source_symbols = [s["symbol"] for s in source["sites"]]
    for candidate in result["candidates"]:
        snap = candidate["snapshot"]
        # kept_indices select exactly the source atoms this candidate holds.
        assert sorted(s["symbol"] for s in snap["sites"]) == sorted(
            source_symbols[i] for i in candidate["kept_indices"]
        )
        assert snap["operation"] == "commit_disorder_candidate"
        assert snap["revision"] == 2  # sibling of the source, not a chain
        assert np.allclose(snap["cell"], source["cell"], atol=1e-9)
        out = tmp_path / f"{candidate['candidate_id']}.cif"
        out.write_text(candidate["cif"], encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            again = analyze_cif(out)
        _same_structure(snap, again)
        assert len(again["sites"]) == len(snap["sites"])
        assert len(again["bonds"]) == len(snap["bonds"])
        # Site and kept positions match the source atoms they were taken from.
        by_kept = sorted(
            tuple(np.round(np.array(source["sites"][i]["fractional_position"]) % 1.0, 4))
            for i in candidate["kept_indices"]
        )
        got = sorted(
            tuple(np.round(np.array(s["fractional_position"]) % 1.0, 4)) for s in snap["sites"]
        )
        assert np.allclose(by_kept, got, atol=1e-3)
        assert json.loads(json.dumps(candidate)) == candidate


def test_candidates_are_independent_not_chained(cif, monkeypatch):
    import molcrys_kit.analysis.disorder.process as process

    from mat_viewer.effects import EffectSession

    calls = {"generate": 0, "operate": []}
    real_operate, real_generate = EffectSession.operate, process.generate_ordered_replicas_from_disordered_sites

    def operate(self, command, params, **kwargs):
        calls.setdefault("sessions", []).append(self)  # keep alive: ids can be reused
        calls["operate"].append((id(self), kwargs["structure_id"], kwargs["expected_revision"]))
        return real_operate(self, command, params, **kwargs)

    def generate(*args, **kwargs):
        calls["generate"] += 1
        calls["kwargs"] = kwargs
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(EffectSession, "operate", operate)
    monkeypatch.setattr(process, "generate_ordered_replicas_from_disordered_sites", generate)
    result = _gen(cif, count=4, independent_assemblies=True)
    assert calls["generate"] == 1  # MolCrysKit solves once; nothing re-derived
    assert calls["kwargs"]["return_kept_indices"] is True
    assert calls["kwargs"]["independent_assemblies"] is True
    assert len(calls["operate"]) == result["actual_count"] == 4
    assert len({sid for sid, _, _ in calls["operate"]}) == 4  # one session each
    assert {rev for _, _, rev in calls["operate"]} == {1}  # always from source rev 1


def test_bond_metadata_is_retained(cif):
    result = _gen(cif, count=4, independent_assemblies=True)
    for candidate in result["candidates"]:
        for site in candidate["snapshot"]["sites"]:
            assert len(site["fractional_position"]) == 3 and len(site["image_shift"]) == 3
            assert {"occupancy", "alpha", "disorder_group", "disorder_assembly"} <= set(site)
        for bond in candidate["snapshot"]["bonds"]:
            assert len(bond["vector_A"]) == 3 and len(bond["right_image_shift"]) == 3
            assert bond["role"]


@pytest.mark.parametrize(
    "kwargs, text",
    [
        ({"method": "optimal", "count": 1}, "operate_commit_major"),
        ({"method": "bogus", "count": 1}, "method must be"),
        ({"method": None, "count": 1}, "method must be"),
        ({"count": 0}, "positive integer"),
        ({"count": -3}, "positive integer"),
        ({"count": True}, "positive integer"),
        ({"count": 2.0}, "positive integer"),
        ({"count": "3"}, "positive integer"),
        ({"count": 17}, "exceeds the phone limit"),
        ({"count": 3, "max_candidates": 2}, "exceeds the phone limit"),
        ({"count": 3, "max_candidates": 99}, "max_candidates"),
        ({"method": "random", "count": 2}, "requires an explicit"),
        ({"method": "random", "count": 2, "seed": -1}, "seed must be"),
        ({"method": "random", "count": 2, "seed": 2**32}, "seed must be"),
        ({"method": "random", "count": 2, "seed": 1.5}, "seed must be"),
        ({"method": "random", "count": 2, "seed": True}, "seed must be"),
        ({"count": 2, "seed": 7}, "only valid with method 'random'"),
        ({"count": 2, "coupled": 1}, "coupled must be a bool"),
        ({"count": 2, "independent_assemblies": "yes"}, "independent_assemblies must be a bool"),
        ({"count": 2, "max_payload_bytes": 0}, "max_payload_bytes"),
    ],
)
def test_invalid_requests_raise_clear_errors(cif, kwargs, text):
    with pytest.raises(CandidateRequestError, match=text):
        generate_disorder_candidates(cif, **kwargs)


def test_missing_and_oversized_sources_raise(tmp_path: Path, monkeypatch):
    with pytest.raises(CandidateRequestError, match="cannot read source CIF"):
        generate_disorder_candidates(tmp_path / "missing.cif", count=1)
    big = tmp_path / "big.cif"
    big.write_text(TWO_ASSEMBLIES, encoding="utf-8")
    import mat_viewer.phone.candidates as module

    monkeypatch.setattr(module, "MAX_SOURCE_BYTES", 100)
    with pytest.raises(CandidateRequestError, match="phone limit"):
        generate_disorder_candidates(big, count=1)


def test_oversized_payload_raises_instead_of_truncating(cif):
    with pytest.raises(CandidatePayloadError, match="request fewer candidates"):
        _gen(cif, count=4, independent_assemblies=True, max_payload_bytes=1024)
    assert issubclass(CandidatePayloadError, ValueError)


def test_ordered_source_yields_one_candidate_without_fabrication(tmp_path: Path):
    path = tmp_path / "ordered.cif"
    path.write_text(ORDERED, encoding="utf-8")
    for method, seed in (("enumerate", None), ("random", 3)):
        try:
            result = _gen(path, method=method, count=3, seed=seed)
        except CandidateRequestError:
            continue  # "no candidate" is an honest failure, not padding
        assert result["actual_count"] <= 3
        assert result["actual_count"] == len(result["candidates"])
        assert result["unique_count"] <= result["actual_count"]


def test_existing_api_is_unchanged(cif):
    assert str(inspect.signature(phone.analyze_cif)) == "(path: 'str | Path') -> 'dict[str, Any]'"
    assert str(inspect.signature(phone.display)) == (
        "(snapshot: 'dict[str, Any]', *, disorder_group: 'str | int | None' = None) "
        "-> 'dict[str, Any]'"
    )
    assert str(inspect.signature(phone.operate_commit_major)) == "(path: 'str | Path') -> 'dict[str, Any]'"
    assert str(inspect.signature(phone.operate_commit_major_with_cif)) == (
        "(path: 'str | Path') -> 'dict[str, Any]'"
    )
    snapshot_before = copy.deepcopy(_gen(cif, count=2)["candidates"][0]["snapshot"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        major = operate_commit_major(cif)
        paired = operate_commit_major_with_cif(cif)
    assert paired["snapshot"] == major and major["revision"] == 2
    assert set(major) == set(snapshot_before)  # candidates reuse the same shape
    assert {"CandidateRequestError", "CandidatePayloadError", "generate_disorder_candidates"} <= set(
        phone.__all__
    )


def test_candidates_path_has_no_desktop_dependencies(cif):
    script = r"""
import json, sys, warnings
blocked = ("ase", "scipy", "matplotlib", "pymatgen", "spglib", "PIL")

class Block:
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in blocked:
            raise ImportError(name)
        return None

sys.meta_path.insert(0, Block())
from mat_viewer.phone import generate_disorder_candidates
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    result = generate_disorder_candidates(
        sys.argv[1], method="random", count=3, seed=5, independent_assemblies=True
    )
loaded = sorted(m for m in sys.modules if m.split(".")[0] in blocked)
print(json.dumps({"n": result["actual_count"], "loaded": loaded, "json": json.dumps(result) != ""}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(cif)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload == {"n": 3, "loaded": [], "json": True}



# --- identity: the set of kept source indices -------------------------------


def _patch_generate(monkeypatch, transform):
    """Run the real MolCrysKit solver once, then reshape its (replica, kept) list."""
    import molcrys_kit.analysis.disorder.process as process

    real = process.generate_ordered_replicas_from_disordered_sites

    def generate(*args, **kwargs):
        return transform(real(*args, **kwargs))

    monkeypatch.setattr(process, "generate_ordered_replicas_from_disordered_sites", generate)


def test_duplicate_identity_ignores_index_order_but_keeps_provenance(cif, monkeypatch):
    def reshape(replicas):
        (r0, k0), (r1, k1) = replicas[0], replicas[1]
        return [
            (r0, list(k0)),
            (r1, list(k1)),
            (r0, list(reversed(k0))),  # same set, different order -> duplicate of 001
            (r1, [k1[1], k1[0], *k1[2:]]),  # same set, swapped -> duplicate of 002
            (r0, list(reversed(k0))),  # exact repeat of 003 -> still duplicate of 001
        ]

    _patch_generate(monkeypatch, reshape)
    result = _gen(cif, count=5)
    kept = _kept(result)
    assert kept == [[0, 1, 2], [0, 3, 4], [2, 1, 0], [3, 0, 4], [2, 1, 0]]  # order untouched
    assert [c["candidate_id"] for c in result["candidates"]] == [
        f"candidate-{i:03d}" for i in range(1, 6)
    ]
    assert [c["index"] for c in result["candidates"]] == [0, 1, 2, 3, 4]
    assert [c["duplicate_of"] for c in result["candidates"]] == [
        None, None, "candidate-001", "candidate-002", "candidate-001",
    ]
    assert result["actual_count"] == 5 and result["unique_count"] == 2
    assert any("3 of 5 candidates repeat" in m for m in _messages(result))


def test_duplicate_identity_random_repeated_samples(cif, monkeypatch):
    import random

    rng = random.Random(20260113)
    pools = [[0, 1, 2], [0, 3, 4], [0, 2, 3], [0, 1, 4]]

    def reshape(replicas):
        replica = replicas[0][0]
        out = []
        for _ in range(16):
            kept = list(rng.choice(pools))
            rng.shuffle(kept)  # same set may arrive in any order
            out.append((replica, kept))
        return out

    _patch_generate(monkeypatch, reshape)
    result = _gen(cif, method="random", count=16, seed=1)
    first: dict[frozenset, str] = {}
    for candidate in result["candidates"]:
        key = frozenset(candidate["kept_indices"])
        assert candidate["duplicate_of"] == first.get(key)
        first.setdefault(key, candidate["candidate_id"])
    assert result["unique_count"] == len(first) <= 4 < result["actual_count"] == 16
    assert len(result["candidates"]) == 16  # nothing dropped
    # Provenance lists are the sampled orders, not sorted copies.
    assert any(c["kept_indices"] != sorted(c["kept_indices"]) for c in result["candidates"])


def test_real_random_samples_use_set_identity(cif):
    result = _gen(cif, method="random", count=16, seed=7)
    seen = {}
    for candidate in result["candidates"]:
        key = frozenset(candidate["kept_indices"])
        assert candidate["duplicate_of"] == seen.get(key)
        seen.setdefault(key, candidate["candidate_id"])
    assert result["unique_count"] == len(seen)


# --- concurrency / isolation ------------------------------------------------


def _global_state():
    import logging

    return (
        list(warnings.filters),
        warnings.showwarning,
        list(logging.getLogger("molcrys_kit").handlers),
        logging.getLogger("molcrys_kit").level,
    )


def test_concurrent_calls_are_isolated_and_restore_global_state(cif):
    from concurrent.futures import ThreadPoolExecutor

    jobs = [
        {"method": "enumerate", "count": 4, "independent_assemblies": True},
        {"method": "enumerate", "count": 5},  # emits MCK's assembly warning
        {"method": "random", "count": 6, "seed": 3},
        {"method": "random", "count": 6, "seed": 4, "coupled": True},
    ] * 3
    expected = [_gen(cif, **job) for job in jobs]  # serial reference
    before = _global_state()
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(lambda job: generate_disorder_candidates(cif, **job), jobs))
    assert got == expected  # no cross-request warnings, ids, or kept lists
    assert _global_state() == before  # no leaked "always" filter or handler


def test_only_calling_thread_records_are_attributed(cif, monkeypatch):
    import logging
    import threading

    log = logging.getLogger("molcrys_kit")

    def reshape(replicas):
        def foreign():
            log.warning("FOREIGN log")
            warnings.warn("FOREIGN warning", UserWarning)

        worker = threading.Thread(target=foreign)
        worker.start()
        worker.join()
        log.warning("OWN log")
        warnings.warn("OWN warning", UserWarning)
        return replicas

    _patch_generate(monkeypatch, reshape)
    before = _global_state()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # keep the foreign warning off the test output
        result = generate_disorder_candidates(cif, count=2)
    messages = _messages(result)
    assert "OWN log" in messages and "OWN warning" in messages
    assert not any("FOREIGN" in m for m in messages)
    assert before[2:] == _global_state()[2:]


def test_capture_state_is_restored_when_generation_fails(cif, monkeypatch):
    def boom(replicas):
        warnings.warn("before failure", UserWarning)
        raise RuntimeError("solver exploded")

    _patch_generate(monkeypatch, boom)
    before = _global_state()
    with pytest.raises(RuntimeError, match="solver exploded"):
        generate_disorder_candidates(cif, count=2)
    assert _global_state() == before
    # A failed call must not leave the lock held for the next one.
    monkeypatch.undo()
    assert _gen(cif, count=2)["actual_count"] == 2


# --- configurable bounds, never truncating ------------------------------------


def test_bounds_are_explicit_configurable_and_never_truncate(cif, tmp_path: Path):
    import mat_viewer.phone.candidates as module

    size = cif.stat().st_size
    with pytest.raises(CandidateRequestError, match="max_source_bytes"):
        _gen(cif, count=1, max_source_bytes=size - 1)
    assert _gen(cif, count=1, max_source_bytes=size)["source"]["bytes"] == size
    for bad in (0, -1, True, 1.5, module.HARD_MAX_SOURCE_BYTES + 1):
        with pytest.raises(CandidateRequestError, match="max_source_bytes"):
            _gen(cif, count=1, max_source_bytes=bad)
    # Above the old hard caps now allowed up to the documented ceilings.
    assert _gen(cif, count=17, max_candidates=17, independent_assemblies=True)["actual_count"] == 4
    assert _gen(cif, count=1, max_payload_bytes=module.HARD_MAX_PAYLOAD_BYTES)["actual_count"] == 1
    with pytest.raises(CandidateRequestError, match="max_candidates"):
        _gen(cif, count=1, max_candidates=module.HARD_MAX_CANDIDATES + 1)
    assert module.MAX_CANDIDATES == 16 and module.MAX_PAYLOAD_BYTES == 8 * 1024 * 1024


def test_payload_limit_is_checked_incrementally(cif, monkeypatch):
    import mat_viewer.phone.candidates as module

    commits = []
    real = module._commit_candidate

    def counting(*args, **kwargs):
        commits.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "_commit_candidate", counting)
    one = len(json.dumps(_gen(cif, count=1)["candidates"][0]))
    with pytest.raises(CandidatePayloadError, match=r"after 1 of \d+ candidates.*max_payload_bytes"):
        _gen(cif, count=4, independent_assemblies=True, max_payload_bytes=one)
    assert len(commits) == 2  # 1 for the sizing call + 1 before stopping, not 4 more
