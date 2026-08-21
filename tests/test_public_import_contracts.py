from __future__ import annotations

import importlib


PUBLIC_IMPORTS = {
    "mat_viewer.api": ("handle_ws_message", "register_api"),
    "mat_viewer.app": ("create_app", "ViewerBackend"),
    "mat_viewer.atom_groups": ("tag_atoms_with_groups",),
    "mat_viewer.bond_groups": ("tag_bonds_with_groups",),
    "mat_viewer.compass": ("camera_screen_basis", "lattice_compass_annotations"),
    "mat_viewer.cube": ("read_cube", "build_cube_figure", "export_static"),
    "mat_viewer.depth_sort": ("camera_view_vector", "assign_zorder_by_depth"),
    "mat_viewer.loader": (
        "LoadedCrystal",
        "build_bundle_scene",
        "build_loaded_crystal",
    ),
    "mat_viewer.math": (
        "camera_screen_basis",
        "ellipsoid_principal_axes",
        "nearest_lattice_shift_frac",
    ),
    "mat_viewer.ortep": ("ellipsoid_principal_axes", "build_ortep_panel_figure"),
    "mat_viewer.perf_log": ("record", "recent", "time_block"),
    "mat_viewer.presets": ("DEFAULT_STYLE", "default_preset", "get_default_catalog"),
    "mat_viewer.render.assembly": ("build_scene_from_atoms",),
    "mat_viewer.renderer": ("build_figure", "uniform_viewport", "render"),
    "mat_viewer.scene": ("build_scene_from_cif", "scene_style", "scene_json"),
    "mat_viewer.scene.state": ("normalize_overlay_overrides",),
    "mat_viewer.scene.store": ("Scene", "SceneStore"),
    "mat_viewer.scenes": ("Scene", "SceneStore"),
    "mat_viewer.structure.cif_parse": ("parse_asu",),
    "mat_viewer.structure.bonds": ("bonds_conflict",),
    "mat_viewer.structure.snapshot": ("molecular_crystal_from_scene",),
    "mat_viewer.topology": ("analyze_topology", "extract_coordination_shell"),
    "mat_viewer.transforms": ("apply_transforms", "transforms_cache_key"),
    "mat_viewer.tui": (
        "TerminalViewController",
        "TerminalCameraState",
        "TerminalDisplayState",
        "TerminalFocusState",
        "TerminalObservation",
        "TerminalViewportState",
        "TerminalViewSnapshot",
        "TerminalViewState",
        "OBSERVATION_SCHEMA",
        "run_tui",
    ),
}


def test_documented_public_imports_remain_available() -> None:
    missing: list[str] = []
    for module_name, names in PUBLIC_IMPORTS.items():
        module = importlib.import_module(module_name)
        for name in names:
            if not hasattr(module, name):
                missing.append(f"{module_name}.{name}")

    assert not missing, "\n".join(missing)
