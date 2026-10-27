"""Terminal entry point for structural operations.

The command delegates to the same EffectSession as the Python API, CLI,
and browser viewer. ``--repeat`` on the loader remains a display copy.
"""

from __future__ import annotations

from .loader_adapter import _load_bundle


def execute_operate_command(crystal, args: list[str]):
    if not args or args[0].lower() in {"supercell", "repeat"}:
        raise ValueError("use 'operate build-supercell A B C'; display repeat is not a structural edit")
    operation = args[0].lower().replace("_", "-")
    if operation != "build-supercell":
        raise ValueError("the terminal currently commits 'operate build-supercell A B C'")
    if len(args) != 4:
        raise ValueError("operate build-supercell requires three positive integers")
    counts = tuple(int(value) for value in args[1:])
    source = getattr(crystal, "source_path", "") or ""
    if not source:
        raise ValueError("this terminal structure has no source file to commit")
    from ..effects.session import EffectSession
    from ..structure.cif_parse import load_cif

    parsed = load_cif(source)
    session = EffectSession()
    ref = session.register(parsed.crystal, structure_id=crystal.title or "structure")
    result = session.operate(
        "build_supercell",
        {"a": counts[0], "b": counts[1], "c": counts[2]},
        structure_id=ref.id,
        expected_revision=ref.revision,
    )
    committed = session.checkout(result.output_ref)
    from ..effects.materialize import loaded_crystal_from_crystal

    bundle = loaded_crystal_from_crystal(committed, name=result.viewer_name or ref.id, title=crystal.title)
    updated = _load_bundle(
        bundle,
        path=source,
        input_format="cif",
        display_mode="unit_cell",
        frame_index=0,
        transforms=None,
    )
    message = (
        f"committed build_supercell {counts[0]}x{counts[1]}x{counts[2]} "
        f"revision {result.output_ref.revision} sites {updated.n_atoms}"
    )
    return message, updated


__all__ = ["execute_operate_command"]
