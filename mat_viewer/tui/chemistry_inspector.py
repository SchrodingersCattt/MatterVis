"""Plain-text chemistry inspector over immutable MolCrysKit records."""

from __future__ import annotations

from collections import Counter

from .inspection import inspect_local_geometry
from .text import terminal_text


def chemistry_warnings(crystal) -> tuple[str, ...]:
    """Return warnings that must remain visible in the primary TUI view."""
    chemistry = crystal.chemistry
    if chemistry is None:
        return ("CHEMISTRY UNAVAILABLE: MolCrysKit records are not attached",)
    values = list(chemistry.warnings)
    if chemistry.status not in {"explicit", "confirmed"}:
        values.insert(
            0,
            f"CHEMISTRY {chemistry.status.upper()}: use :why for evidence and alternatives",
        )
    return tuple(dict.fromkeys(str(value) for value in values))


def format_atom_inspector(crystal, index: int, *, view: str = "full") -> str:
    """Format one selected atom without performing chemical inference."""
    if view not in {"full", "stereo", "why", "name"}:
        raise ValueError("inspector view must be full, stereo, why, or name")
    if not 0 <= index < len(crystal.atoms):
        raise ValueError("selected atom index is outside the displayed structure")
    atom = crystal.atoms[index]
    chemistry = crystal.chemistry
    atom_record = chemistry.atom(atom.atom_id) if chemistry and atom.atom_id else None
    entity = (
        chemistry.entity(atom_record.entity_id)
        if chemistry is not None and atom_record is not None
        else None
    )

    if view == "stereo":
        return _stereo_text(crystal, atom, atom_record)
    if view == "why":
        return _why_text(chemistry, atom, atom_record, entity)
    if view == "name":
        return _name_text(chemistry, entity)

    geometry = inspect_local_geometry(
        crystal,
        _exact_reference(atom, index),
        include_angles=True,
    )
    lines = [
        f"ATOM [{terminal_text(atom.display_label)}]",
        f"id: {terminal_text(atom.atom_id) or 'unavailable'}",
        _atom_chemistry_line(atom_record),
        f"occupancy: {atom.occupancy:g}  disorder: {atom.disorder_group or 'ordered'}",
        (
            f"site: source={atom.source_index} symop={atom.symmetry_operation_index} "
            f"image={_shift_text(atom.image_shift)}"
        ),
        f"cart A: {_vector_text(atom.cart)}",
        f"frac: {_vector_text(atom.frac)}",
        "",
    ]
    lines.extend(_entity_lines(chemistry, entity))
    lines.extend(["", *_bond_lines(crystal, atom, geometry)])
    lines.extend(["", *_stereo_text(crystal, atom, atom_record).splitlines()])
    lines.extend(["", *_crystal_lines(crystal, chemistry)])
    warnings = _relevant_warnings(chemistry, entity)
    if warnings:
        lines.extend(["", "WARNINGS", *(f"! {warning}" for warning in warnings)])
    return "\n".join(lines)


def _atom_chemistry_line(record) -> str:
    if record is None:
        return "chemistry: unavailable (no MolCrysKit atom record)"
    isotope = "natural" if record.isotope is None else str(record.isotope)
    charge = "?" if record.formal_charge is None else _signed(record.formal_charge)
    oxidation = (
        "?" if record.oxidation_state is None else _signed(record.oxidation_state)
    )
    hydrogens = (
        "?" if record.implicit_hydrogens is None else str(record.implicit_hydrogens)
    )
    radical = (
        f"  radical-e={record.radical_electrons}" if record.radical_electrons else ""
    )
    return (
        f"element: {record.element}  isotope: {isotope}  charge: {charge}  "
        f"oxidation: {oxidation}  implicit-H: {hydrogens}{radical} [{record.status}]"
    )


def _entity_lines(chemistry, entity) -> list[str]:
    if chemistry is None or entity is None:
        return ["ENTITY", "unavailable (MolCrysKit entity record not attached)"]
    dimension = "?" if entity.dimension is None else f"{entity.dimension}D"
    charge = "?" if entity.net_charge is None else _signed(entity.net_charge)
    lines = [
        "ENTITY",
        f"{entity.entity_id}  {entity.kind}  dimension={dimension}",
        f"formula: {_entity_formula(chemistry, entity)}  charge={charge} [{entity.status}]",
    ]
    if entity.translation_generators:
        lines.append(
            "translations: "
            + ", ".join(_shift_text(vector) for vector in entity.translation_generators)
        )
    lines.extend(_name_lines(chemistry))
    lines.append("line notation: unavailable (no MCK naming record)")
    return lines


def _bond_lines(crystal, atom, geometry) -> list[str]:
    lines = [f"BONDS ({geometry['coordination_number']})"]
    chemistry = crystal.chemistry
    for bond in geometry["bonds"]:
        neighbor_id = bond.get("neighbor_atom_id", "")
        chemical_bond = _find_bond(chemistry, atom.atom_id, neighbor_id)
        lines.append(
            f"- {bond['neighbor_label']}  {_bond_semantics(chemical_bond)}  "
            f"{bond['mic_distance']:.3f} A image={_shift_text(tuple(bond['nearest_image_shift']))}"
        )
    if not geometry["bonds"]:
        lines.append("- none")
    lines.append("coordination geometry: unavailable (not supplied by MolCrysKit)")
    ring_lines = _ring_lines(crystal, atom.source_index)
    lines.append("rings: " + ("; ".join(ring_lines) if ring_lines else "none reported"))
    if geometry["angles"]:
        angles = ", ".join(
            f"{'-'.join(item['atoms'])}={item['angle_deg']:.1f} deg"
            for item in geometry["angles"][:6]
        )
        if len(geometry["angles"]) > 6:
            angles += f", +{len(geometry['angles']) - 6} more"
        lines.append(f"angles: {angles}")
    return lines


def _stereo_text(crystal, atom, record) -> str:
    lines = [f"STEREO [{terminal_text(atom.display_label)}]"]
    if record is None:
        lines.append("unavailable: no MolCrysKit stereochemistry record")
        return "\n".join(lines)
    if record.stereo_kind is None:
        lines.append("not identified as a supported stereogenic unit")
        return "\n".join(lines)
    descriptor = record.stereo_descriptor or "indeterminate"
    lines.append(
        f"{record.stereo_kind}: {descriptor} [{record.stereo_status or record.status}]"
    )
    if record.cip_order:
        labels = [_atom_label_for_id(crystal, atom_id) for atom_id in record.cip_order]
        lines.append("CIP: " + " > ".join(labels))
    if record.stereo_reason:
        lines.append("reason: " + terminal_text(record.stereo_reason))
    return "\n".join(lines)


def _why_text(chemistry, atom, record, entity) -> str:
    lines = [f"WHY [{terminal_text(atom.display_label)}]"]
    if chemistry is None:
        return "\n".join([*lines, "MolCrysKit chemistry records are unavailable."])
    lines.append(f"crystal chemistry: {chemistry.status}; source={chemistry.source}")
    lines.append(f"alternative interpretations retained: {chemistry.alternative_count}")
    if record is not None:
        lines.append(f"atom status: {record.status}")
        lines.extend(f"atom evidence: {value}" for value in record.evidence)
        if record.stereo_reason:
            lines.append(f"stereo reason: {record.stereo_reason}")
    if entity is not None:
        lines.append(f"entity status: {entity.status}")
        lines.extend(f"entity evidence: {value}" for value in entity.evidence)
    lines.extend(f"crystal evidence: {value}" for value in chemistry.evidence)
    lines.extend(f"WARNING: {value}" for value in _relevant_warnings(chemistry, entity))
    return "\n".join(lines)


def _name_text(chemistry, entity) -> str:
    lines = ["IUPAC NAME"]
    if chemistry is None:
        return "\n".join(
            [*lines, "unavailable: MolCrysKit chemistry records are absent"]
        )
    lines.extend(_name_lines(chemistry))
    if entity is not None:
        lines.append(f"entity: {entity.entity_id}")
    return "\n".join(lines)


def _name_lines(chemistry) -> list[str]:
    # CIF-deposited names are provenance, not output from the future MCK
    # standards-traced naming engine. Keep that distinction visible.
    if not chemistry.source_names:
        return ["IUPAC name: unavailable (no MCK naming record)"]
    return [
        "IUPAC name: unavailable (source CIF names are not revalidated)",
        *(
            f"CIF {kind} name: {terminal_text(value)}"
            for kind, value in chemistry.source_names
        ),
    ]


def _crystal_lines(crystal, chemistry) -> list[str]:
    lines = [
        "CRYSTAL",
        f"formula: {terminal_text(crystal.canonical_formula or crystal.formula)}",
        f"space group: {terminal_text(crystal.spacegroup) or 'unavailable'}",
        "enantiomer composition: unavailable (no MCK crystal stereo report)",
    ]
    if chemistry is None:
        lines.append("absolute structure evidence: unavailable")
        return lines
    if chemistry.absolute_configuration:
        lines.append(f"CIF absolute configuration: {chemistry.absolute_configuration}")
    if chemistry.absolute_structure:
        for record in chemistry.absolute_structure:
            uncertainty = (
                ""
                if record.standard_uncertainty is None
                else f"; su={record.standard_uncertainty:g}"
            )
            lines.append(
                f"{record.method}: {record.raw} (value={record.value:g}{uncertainty})"
            )
    else:
        lines.append("absolute structure evidence: not reported")
    if chemistry.absolute_structure_details:
        lines.append(
            f"absolute structure details: {chemistry.absolute_structure_details}"
        )
    return lines


def _find_bond(chemistry, left_id: str, right_id: str):
    if chemistry is None or not left_id or not right_id:
        return None
    return next(
        (
            bond
            for bond in chemistry.bonds
            if {bond.atom1_id, bond.atom2_id} == {left_id, right_id}
        ),
        None,
    )


def _bond_semantics(record) -> str:
    if record is None:
        return "chemistry-unavailable"
    order = "?" if record.order is None else f"{record.order:g}"
    aromatic = " aromatic" if record.aromatic else ""
    stereo = (
        "" if record.stereochemistry is None else f" stereo={record.stereochemistry}"
    )
    return f"{record.kind} order={order}{aromatic}{stereo}"


def _ring_lines(crystal, source_index: int) -> list[str]:
    records = []
    for ring in crystal.metadata.get("rings", ()):
        if source_index not in ring.get("atom_indices", ()):
            continue
        aromatic = "aromatic" if ring.get("is_aromatic") else "non-aromatic"
        planar = "planar" if ring.get("is_planar") else "non-planar"
        records.append(f"{ring.get('size', '?')}-member {aromatic} {planar}")
    return records


def _entity_formula(chemistry, entity) -> str:
    records = [chemistry.atom(atom_id) for atom_id in entity.atom_ids]
    counter: Counter[str] = Counter()
    for record in records:
        if record is None:
            continue
        symbol = (
            record.element
            if record.isotope is None
            else f"[{record.isotope}{record.element}]"
        )
        counter[symbol] += 1
        if record.implicit_hydrogens:
            counter["H"] += record.implicit_hydrogens
    ordered = sorted(
        counter,
        key=_formula_sort_key,
    )
    return (
        "".join(
            symbol + (str(counter[symbol]) if counter[symbol] != 1 else "")
            for symbol in ordered
        )
        or "?"
    )


def _formula_sort_key(symbol: str) -> tuple[int, str, str]:
    bare = symbol
    if symbol.startswith("[") and symbol.endswith("]"):
        bare = "".join(character for character in symbol if character.isalpha())
    return (0 if bare == "C" else 1 if bare == "H" else 2, bare, symbol)


def _atom_label_for_id(crystal, atom_id: str) -> str:
    if atom_id.endswith(":implicit-H"):
        return "implicit-H"
    atom = next((item for item in crystal.atoms if item.atom_id == atom_id), None)
    return (
        terminal_text(atom.display_label)
        if atom is not None
        else terminal_text(atom_id)
    )


def _relevant_warnings(chemistry, entity) -> tuple[str, ...]:
    if chemistry is None:
        return ("MolCrysKit chemistry records are unavailable",)
    values = [*chemistry.warnings]
    if entity is not None:
        values.extend(entity.warnings)
    return tuple(dict.fromkeys(terminal_text(value) for value in values))


def _exact_reference(atom, index: int):
    if atom.display_copy_id:
        return {"display_copy_id": atom.display_copy_id}
    if atom.source_index >= 0:
        return atom.source_index
    return {"label": atom.label} if atom.label else index


def _signed(value: int) -> str:
    return f"{value:+d}"


def _shift_text(value) -> str:
    return "(" + ",".join(f"{int(item):+d}" for item in value) + ")"


def _vector_text(value) -> str:
    return "(" + ", ".join(f"{float(item):.4f}" for item in value) + ")"


__all__ = ["chemistry_warnings", "format_atom_inspector"]
