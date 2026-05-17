from __future__ import annotations

import importlib


PUBLIC_IMPORTS = {
    "crystal_viewer.api": ("handle_ws_message", "register_api"),
    "crystal_viewer.app": ("create_app", "ViewerBackend"),
    "crystal_viewer.atom_groups": ("tag_atoms_with_groups",),
    "crystal_viewer.bond_groups": ("tag_bonds_with_groups",),
    "crystal_viewer.compass": ("camera_screen_basis", "lattice_compass_annotations"),
    "crystal_viewer.cube": ("read_cube", "build_orbital_panel_figure", "export_static"),
    "crystal_viewer.loader": ("LoadedCrystal", "build_bundle_scene", "build_loaded_crystal"),
    "crystal_viewer.ortep": ("ellipsoid_principal_axes", "build_ortep_panel_figure"),
    "crystal_viewer.renderer": ("build_figure", "uniform_viewport"),
    "crystal_viewer.scene": ("build_scene_from_cif", "scene_style", "scene_json"),
    "crystal_viewer.static_publication.plot_crystal": ("parse_asu", "find_bonds", "draw_scene"),
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
