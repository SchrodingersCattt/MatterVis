"""Phase 5 polyhedron-spec knobs unlocked by MCK 0.4's ``find_polyhedra``.

Each spec now carries ``level``, ``center_kind``, ``hard_cutoff`` and
``fallback_max`` in addition to the historical ``enforce_enclosure`` /
``centroid_offset_frac`` pair. The schema, REST surface and MCK
passthrough are part of the public API; this file is the contract test
for all three (cf. ``agents/polyhedron_api.md``).
"""
from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import WORKSPACE_DIR, create_app
from crystal_viewer.app.normalizers import _normalize_polyhedron_spec


def _client(tmp_path: Path):
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=WORKSPACE_DIR,
    )
    return app.server.test_client()


# ----------------------------------------------------------------------
# Normaliser-level contracts
# ----------------------------------------------------------------------


def test_normalize_defaults_to_molecule_and_natural_shell():
    spec = _normalize_polyhedron_spec(
        {"center_species": "N H4", "ligand_species": "Cl O4"},
        fallback_color="#7c5cbf",
        existing_ids=set(),
    )
    assert spec is not None
    # Default level keeps the historical "molecule packing" semantics
    # so existing scripts stay on the natural first-shell path.
    assert spec["level"] == "molecule"
    assert spec["center_kind"] == "centroid"
    # ``None`` means MCK's natural gap+enclosure first shell; do not
    # silently default to a hard cap.
    assert spec["hard_cutoff"] is None
    assert spec["fallback_max"] is None


def test_normalize_atom_level_drops_hard_cutoff():
    spec = _normalize_polyhedron_spec(
        {
            "center_species": "Pb",
            "ligand_species": "I",
            "level": "atom",
            "hard_cutoff": 5.0,
            "fallback_max": 6,
        },
        fallback_color="#7c5cbf",
        existing_ids=set(),
    )
    assert spec is not None
    assert spec["level"] == "atom"
    # MCK rejects hard_cutoff at atom level (cutoff= is already the hard
    # cap on that level). MV drops it at the boundary so a careless
    # caller can't crash the upstream API.
    assert spec["hard_cutoff"] is None
    # fallback_max is still meaningful on either level.
    assert spec["fallback_max"] == 6


def test_normalize_clamps_invalid_hard_cutoff_to_none():
    spec = _normalize_polyhedron_spec(
        {
            "center_species": "N H4",
            "ligand_species": "Cl O4",
            "hard_cutoff": -1.0,
        },
        fallback_color="#7c5cbf",
        existing_ids=set(),
    )
    assert spec["hard_cutoff"] is None
    spec_zero = _normalize_polyhedron_spec(
        {
            "center_species": "N H4",
            "ligand_species": "Cl O4",
            "hard_cutoff": 0,
        },
        fallback_color="#7c5cbf",
        existing_ids=set(),
    )
    assert spec_zero["hard_cutoff"] is None


def test_normalize_rejects_unknown_level_and_center_kind():
    spec = _normalize_polyhedron_spec(
        {
            "center_species": "N H4",
            "ligand_species": "Cl O4",
            "level": "lattice",
            "center_kind": "wat",
        },
        fallback_color="#7c5cbf",
        existing_ids=set(),
    )
    assert spec["level"] == "molecule"
    assert spec["center_kind"] == "centroid"


# ----------------------------------------------------------------------
# REST contract
# ----------------------------------------------------------------------


def test_polyhedra_post_roundtrips_new_knobs(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v2/polyhedra",
        json={
            "center_species": "N H4",
            "ligand_species": "Cl O4",
            "level": "molecule",
            "center_kind": "com",
            "hard_cutoff": 8.0,
            "fallback_max": 12,
            "centroid_offset_frac": 0.2,
        },
    )
    assert response.status_code == 200
    spec = response.get_json()
    assert spec["level"] == "molecule"
    assert spec["center_kind"] == "com"
    assert spec["hard_cutoff"] == 8.0
    assert spec["fallback_max"] == 12
    listing = client.get("/api/v2/polyhedra").get_json()
    assert listing["specs"][0]["hard_cutoff"] == 8.0


def test_polyhedra_patch_can_flip_level_to_atom(tmp_path: Path):
    client = _client(tmp_path)
    created = client.post(
        "/api/v2/polyhedra",
        json={
            "center_species": "Pb",
            "ligand_species": "I",
            "level": "molecule",
            "hard_cutoff": 5.0,
        },
    )
    spec_id = created.get_json()["id"]
    patched = client.patch(
        f"/api/v2/polyhedra/{spec_id}",
        json={"level": "atom", "fallback_max": 6},
    )
    assert patched.status_code == 200
    spec = patched.get_json()
    assert spec["level"] == "atom"
    # Atom level drops hard_cutoff regardless of what was persisted.
    assert spec["hard_cutoff"] is None
    assert spec["fallback_max"] == 6


# ----------------------------------------------------------------------
# MCK passthrough -- verify the new knobs end up on find_polyhedra(...).
# ----------------------------------------------------------------------


def test_find_polyhedra_receives_hard_cutoff_and_center_kind(monkeypatch):
    """Smoke-check the topology pipeline forwards new knobs to MCK."""
    import crystal_viewer.topology as topology_public
    from crystal_viewer.topology import analysis as topology_analysis

    captured: dict[str, object] = {}

    def fake_find_polyhedra(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return []

    # The analysis module resolves ``find_polyhedra`` via
    # ``getattr(crystal_viewer.topology, "find_polyhedra", ...)`` so
    # tests inject their fake on the public module.
    monkeypatch.setattr(topology_public, "find_polyhedra", fake_find_polyhedra)
    monkeypatch.setattr(topology_analysis, "find_polyhedra", fake_find_polyhedra)

    class _StubBundle:
        molcrys_analysis = None

    fragment = {
        "index": 0,
        "label": "NH4-1",
        "formula": "N H4",
        "species": "N H4",
        "center": [0.0, 0.0, 0.0],
        "source_molecule_index": 0,
    }

    class _FakeCrystal:
        pass

    monkeypatch.setattr(
        topology_analysis.molcrys_bridge,
        "molecular_crystal_from_bundle",
        lambda bundle: _FakeCrystal(),
    )
    monkeypatch.setattr(
        topology_analysis.molcrys_bridge,
        "formula_to_moiety",
        lambda formula: formula,
    )

    topology_analysis._mck_polyhedron_record(
        _StubBundle(),
        fragment,
        cutoff=10.0,
        ligand_species=("Cl O4",),
        level="molecule",
        enforce_enclosure=True,
        centroid_offset_frac=0.15,
        center_kind="com",
        hard_cutoff=8.0,
        fallback_max=12,
    )
    kwargs = captured["kwargs"]
    assert kwargs["level"] == "molecule"
    assert kwargs["center_kind"] == "com"
    assert kwargs["hard_cutoff"] == 8.0
    assert kwargs["fallback_max"] == 12
    # ``cutoff=`` continues to mean "candidate search radius" on molecule
    # level (MCK PR #32); do not collapse it into hard_cutoff.
    assert kwargs["cutoff"] == 10.0


def test_find_polyhedra_atom_level_skips_hard_cutoff(monkeypatch):
    import crystal_viewer.topology as topology_public
    from crystal_viewer.topology import analysis as topology_analysis

    captured: dict[str, object] = {}

    def fake_find_polyhedra(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(topology_public, "find_polyhedra", fake_find_polyhedra)
    monkeypatch.setattr(topology_analysis, "find_polyhedra", fake_find_polyhedra)

    class _StubBundle:
        molcrys_analysis = None

    fragment = {
        "index": 0,
        "label": "Pb-1",
        "formula": "Pb",
        "species": "Pb",
        "center": [0.0, 0.0, 0.0],
        "elem_set": ["Pb"],
    }

    monkeypatch.setattr(
        topology_analysis.molcrys_bridge,
        "molecular_crystal_from_bundle",
        lambda bundle: object(),
    )
    topology_analysis._mck_polyhedron_record(
        _StubBundle(),
        fragment,
        cutoff=3.5,
        ligand_species=("I",),
        level="atom",
        center_species="Pb",
        enforce_enclosure=True,
        centroid_offset_frac=0.15,
        center_kind="com",  # ignored on atom level
        hard_cutoff=99.0,    # MUST NOT appear in MCK call
        fallback_max=6,
    )
    kwargs = captured["kwargs"]
    assert kwargs["level"] == "atom"
    assert "hard_cutoff" not in kwargs
    assert kwargs["fallback_max"] == 6
    # ``cutoff=`` on atom level still means the hard radial cap.
    assert kwargs["cutoff"] == 3.5
