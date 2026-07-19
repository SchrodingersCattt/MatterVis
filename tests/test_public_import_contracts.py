from __future__ import annotations

import importlib


PUBLIC_IMPORTS = {
    "crystal_viewer.api": ("handle_ws_message", "register_api"),
    "crystal_viewer.app": ("create_app", "ViewerBackend"),
    "crystal_viewer.atom_groups": ("tag_atoms_with_groups",),
    "crystal_viewer.bond_groups": ("tag_bonds_with_groups",),
    "crystal_viewer.compass": ("camera_screen_basis", "lattice_compass_annotations"),
    "crystal_viewer.cube": ("read_cube", "build_orbital_panel_figure", "export_static", "mapped_isosurface_mesh_trace"),
    "crystal_viewer.depth_sort": ("camera_view_vector", "assign_zorder_by_depth"),
    "crystal_viewer.loader": ("LoadedCrystal", "build_bundle_scene", "build_loaded_crystal"),
    "crystal_viewer.math": ("camera_screen_basis", "ellipsoid_principal_axes", "nearest_lattice_shift_frac"),
    "crystal_viewer.ortep": ("ellipsoid_principal_axes", "build_ortep_panel_figure"),
    "crystal_viewer.perf_log": ("record", "recent", "time_block"),
    "crystal_viewer.presets": ("DEFAULT_STYLE", "default_preset", "get_default_catalog"),
    "crystal_viewer.render.assembly": ("build_scene_from_atoms",),
    "crystal_viewer.renderer": ("build_figure", "uniform_viewport", "render"),
    "crystal_viewer.scene": ("build_scene_from_cif", "scene_style", "scene_json"),
    "crystal_viewer.scene.state": ("normalize_overlay_overrides",),
    "crystal_viewer.scene.store": ("Scene", "SceneStore"),
    "crystal_viewer.scenes": ("Scene", "SceneStore"),
    "crystal_viewer.structure.cif_parse": ("parse_asu",),
    "crystal_viewer.structure.bonds": ("find_bonds", "bonds_conflict"),
    "crystal_viewer.structure.snapshot": ("molecular_crystal_from_scene",),
    "crystal_viewer.topology": ("analyze_topology", "extract_coordination_shell"),
    "crystal_viewer.transforms": ("apply_transforms", "transforms_cache_key"),
}


def test_documented_public_imports_remain_available() -> None:
    missing: list[str] = []
    for module_name, names in PUBLIC_IMPORTS.items():
        module = importlib.import_module(module_name)
        for name in names:
            if not hasattr(module, name):
                missing.append(f"{module_name}.{name}")

    assert not missing, "\n".join(missing)
