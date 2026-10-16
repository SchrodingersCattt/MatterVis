from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

import numpy as np

def _partial_occupancy_value(atom: dict[str, Any]) -> float:
    try:
        return float(atom.get("occ", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _blank_disorder_tags(atom: dict[str, Any]) -> bool:
    dg = str(atom.get("dg") or ".").strip()
    da = str(atom.get("da") or ".").strip()
    # The CIF parser normalises an omitted disorder group to either "." or
    # "0", depending on the input dialect. Both mean "no explicit group".
    return dg in (".", "?", "", "0") and da in (".", "?", "", "0")


def _site_label(atom: dict[str, Any]) -> str:
    return str(atom.get("_asym_label") or atom.get("label") or "")


def _occupancy_disorder_label_stem(label: str) -> str:
    match = re.match(r"^([A-Za-z]+[0-9]+)", label)
    return match.group(1) if match else label


def _mixed_occupancy_site_indices(raw_atoms) -> set[int]:
    """Find mixed-element alternatives occupying one crystallographic site.

    Spinel and oxide CIFs frequently encode a site such as ``Mg0.5Fe0.5`` as
    two partial records with different labels and blank disorder tags.  Label
    stems cannot associate those records, but MolCrysKit preserves the ASU
    index and fractional position.  Treat a same-site, mixed-element group as
    explicit disorder while retaining the conservative lone-special-position
    rule for a single partial record.
    """
    groups: dict[tuple[float, float, float], list[tuple[int, str]]] = defaultdict(list)
    for index, atom in enumerate(raw_atoms or ()):
        if _partial_occupancy_value(atom) >= 0.999:
            continue
        try:
            frac = np.asarray(atom.get("frac"), dtype=float)
        except (TypeError, ValueError):
            continue
        if frac.shape != (3,) or not np.all(np.isfinite(frac)):
            continue
        key = tuple(np.round(frac - np.floor(frac), 5))
        groups[key].append((index, str(atom.get("elem") or "").strip()))
    out: set[int] = set()
    for records in groups.values():
        elements = {element for _, element in records if element}
        if len(records) >= 2 and len(elements) >= 2:
            out.update(index for index, _ in records)
    return out


def _occupancy_only_disorder_indices(raw_atoms) -> set[int]:
    """Return indices likely belonging to blank-tag occupancy disorder.

    A single partial-occupancy site with blank disorder tags is ambiguous:
    it may be an ordered atom on a special position. SHELX occupancy-only
    disorder usually appears as sibling labels such as H3A/H3B or C8/C8A,
    so require at least two distinct source labels with the same stem before
    invoking the ordered-replica solver or tagging atoms minor.
    """
    by_stem: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for idx, atom in enumerate(raw_atoms):
        if _partial_occupancy_value(atom) >= 0.999 or not _blank_disorder_tags(atom):
            continue
        label = _site_label(atom)
        if not label:
            continue
        by_stem[_occupancy_disorder_label_stem(label)][label].append(idx)

    out: set[int] = set(_mixed_occupancy_site_indices(raw_atoms))
    for labels in by_stem.values():
        if len(labels) < 2:
            continue
        for indices in labels.values():
            out.update(indices)
    return out


def _explicit_assembly_disorder_indices(raw_atoms) -> set[int]:
    """Return indices of atoms participating in the third SHELX disorder
    shape: partial occupancy with non-blank
    ``_atom_site_disorder_assembly`` AND
    ``_atom_site_disorder_group``, restricted to assemblies that
    actually carry multiple competing groups.

    This is what Olex2 / SHELX honestly write for two-position disorder
    (HPEP: A/1+A/2, B/1+B/2, C/1+C/2 with paired occupancies summing to
    ~1.0). The older detector only looked for the SHELX "-PART"
    sign convention (``dg = "-1"``) and the blank-tag rotamer form, so
    HPEP-shaped CIFs rendered every disorder twin at full opacity --
    the user-visible "无序的透明度没了" bug.

    A single (assembly, group) bucket on its own (e.g. one
    half-occupied site sitting on a special position with a single
    explicit group) is *not* enough evidence to flag the atom as a
    disorder twin; we require at least two distinct group ids inside
    the same assembly so an ordered special-position site doesn't get
    mistaken for one half of a vanished partner.
    """
    by_assembly: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for idx, atom in enumerate(raw_atoms):
        if _partial_occupancy_value(atom) >= 0.999:
            continue
        dg = str(atom.get("dg") or ".").strip()
        da = str(atom.get("da") or ".").strip()
        if dg in (".", "?", "") or da in (".", "?", ""):
            continue
        by_assembly[da][dg].append(idx)
    out: set[int] = set()
    for groups in by_assembly.values():
        if len(groups) < 2:
            continue
        for indices in groups.values():
            out.update(indices)
    return out


def _explicit_group_disorder_indices(raw_atoms) -> set[int]:
    """Return partial sites carrying a non-blank CIF disorder group.

    A number of refinement programs emit ``_atom_site_disorder_group`` but
    omit ``_atom_site_disorder_assembly``.  Group identifiers are explicit
    disorder provenance even without the optional assembly namespace; the
    previous detector therefore missed these partial sites entirely.  Keep a
    lone partial site with *both* tags blank as the ordered-special-position
    case handled by :func:`_occupancy_only_disorder_indices`.
    """
    out: set[int] = set()
    for idx, atom in enumerate(raw_atoms):
        if _partial_occupancy_value(atom) >= 0.999:
            continue
        dg = _normal_disorder_tag(atom.get("dg"))
        da = _normal_disorder_tag(atom.get("da"))
        # Explicit assemblies are handled by
        # ``_explicit_assembly_disorder_indices``; this helper covers the
        # group-only dialect where assembly is omitted.  A nonzero group is
        # itself explicit provenance even when its competing partner is
        # represented by symmetry or omitted from the exported CIF.
        if da not in (".", "?", "") or dg in (".", "?", "", "0"):
            continue
        out.add(idx)
    return out


def _has_explicit_assembly_disorder(raw_atoms) -> bool:
    return bool(_explicit_assembly_disorder_indices(raw_atoms))


def _has_shelx_occupancy_disorder(raw_atoms) -> bool:
    """Return True if ``raw_atoms`` contains any SHELX-style disorder
    that needs MolCrysKit's optimal-replica picker to resolve.

    Three patterns trigger this:

    1. *Occupancy-only* disorder: sibling labels with ``occ < 1`` and both
       ``_atom_site_disorder_group`` and ``_atom_site_disorder_assembly``
       blank (both default to "."). SHELX often writes rotamer pairs
       (NH4+ in DAP-4, perchlorate H atoms in SY's older revisions)
       this way, summing two occupancies to 1 with no other markers.
    2. *SHELX -PART convention*: ``occ < 1`` with ``dg`` starting with
       ``"-"`` (e.g. ``"-1"``). The other half of the disorder pair
       lives at the symmetry equivalent of this atom; after
       :func:`parse_asu` symmetry expansion both alternatives are
       present as separate atoms, but neither carries the marker that
       distinguishes them. MolCrysKit's neighbour-list will then bond
       the two alternatives together (N3 / N2 at 0.15 A apart in SY),
       fusing two chemically distinct cations into one species.
    3. *Explicit assembly + group* disorder: ``occ < 1`` with non-blank
       ``_atom_site_disorder_assembly`` AND ``_atom_site_disorder_group``
       where the same assembly has multiple competing groups (Olex2's
       standard A/1+A/2, B/1+B/2 form, e.g. HPEP). Without this branch
       the loader silently treats both alternatives as full-occupancy
       major atoms and the disorder='opacity' style has nothing to fade.
    4. *Group-only* disorder: ``occ < 1`` with a nonzero
       ``_atom_site_disorder_group`` and blank assembly. Some exporters omit
       the optional assembly field even when the group token is explicit;
       the token still carries disorder provenance.

    In all three cases ``parse_asu`` alone cannot resolve the disorder,
    so we hand the CIF to MolCrysKit's optimal-replica picker and tag
    the non-chosen alternates with ``_is_minor=True`` so the bond graph
    sees one consistent set per disorder site.
    """
    for atom in raw_atoms:
        occ = _partial_occupancy_value(atom)
        if occ >= 0.999:
            continue
        dg = str(atom.get("dg") or ".").strip()
        if dg.startswith("-") and dg not in ("-",):
            return True
    if _has_explicit_assembly_disorder(raw_atoms):
        return True
    if _explicit_group_disorder_indices(raw_atoms):
        return True
    return bool(_occupancy_only_disorder_indices(raw_atoms))


_PARTIAL_SOLVENT_LABEL = re.compile(r"^[OH]\d+W", re.IGNORECASE)


def _tag_unresolved_partial_disorder(raw_atoms):
    """Record provenance for recognised disorder left unresolved.

    The ordered-replica resolver may be unable to choose between equal-weight
    alternatives. Keep every recognised alternative and let occupancy drive
    its visual weight. A lone partial-occupancy special-position site remains
    ordered: occupancy alone is not sufficient evidence of disorder.
    """
    out = [dict(atom) for atom in raw_atoms]
    recognised = _occupancy_only_disorder_indices(out)
    recognised.update(_explicit_assembly_disorder_indices(out))
    recognised.update(_explicit_group_disorder_indices(out))
    for index, atom in enumerate(out):
        if "_is_minor" in atom:
            atom["is_disordered"] = True
            atom["disorder_resolved"] = True
            continue
        dg = str(atom.get("dg") or ".").strip()
        shelx_part = dg.startswith("-") and dg not in ("-",)
        partial_solvent = _PARTIAL_SOLVENT_LABEL.match(_site_label(atom))
        if _partial_occupancy_value(atom) < 0.999 and (
            index in recognised or shelx_part or partial_solvent
        ):
            atom["is_disordered"] = True
            atom["disorder_resolved"] = False
    return out


def _normal_disorder_tag(value: Any) -> str:
    return str(value or ".").strip()


def _has_shelx_part_disorder(raw_atoms) -> bool:
    for atom in raw_atoms:
        if _partial_occupancy_value(atom) >= 0.999:
            continue
        dg = _normal_disorder_tag(atom.get("dg"))
        if dg.startswith("-") and dg not in ("-",):
            return True
    return False


def _simple_explicit_assembly_major_groups(raw_atoms) -> dict[str, set[str]] | None:
    """Return occupancy-major groups for simple explicit assemblies.

    This is a fast path for the common Olex2/SHELX shape where a CIF
    already carries ``_atom_site_disorder_assembly`` +
    ``_atom_site_disorder_group`` and one group has a strictly larger
    crystallographic occupancy than the others. In that case the major
    orientation is determined by the CIF itself; running MolCrysKit's
    ordered-replica optimiser first and then overriding it by occupancy
    wastes seconds on large structures.

    Equal-occupancy groups and mixed disorder forms return ``None`` so
    the existing full MolCrysKit fallback still owns chemically
    ambiguous cases.
    """
    if _has_shelx_part_disorder(raw_atoms) or _occupancy_only_disorder_indices(
        raw_atoms
    ):
        return None
    by_assembly: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for atom in raw_atoms:
        occ = _partial_occupancy_value(atom)
        dg = _normal_disorder_tag(atom.get("dg"))
        da = _normal_disorder_tag(atom.get("da"))
        # Conservative first cut: only use the cheap occupancy-major
        # assignment when the CIF describes the whole structure as explicit
        # alternatives. Partial-site disorder embedded in otherwise ordered
        # atoms can affect molecule grouping/formula-unit selection; leave
        # those cases to MolCrysKit's full ordered-replica path.
        if occ >= 0.999 or dg in (".", "?", "") or da in (".", "?", ""):
            return None
        if dg in (".", "?", "") or da in (".", "?", ""):
            continue
        by_assembly[da][dg].append(occ)
    if not by_assembly:
        return None

    major_by_assembly: dict[str, set[str]] = {}
    for da, groups in by_assembly.items():
        if len(groups) < 2:
            continue
        averages = {
            dg: sum(values) / len(values) for dg, values in groups.items() if values
        }
        if len(averages) < 2:
            continue
        ranked = sorted(averages.items(), key=lambda item: item[1], reverse=True)
        if ranked[0][1] <= ranked[1][1] + 1e-4:
            return None
        major_by_assembly[da] = {ranked[0][0]}
    return major_by_assembly or None


def _tag_simple_explicit_assembly_disorder(
    raw_atoms,
    major_by_assembly: dict[str, set[str]],
):
    out = [dict(atom) for atom in raw_atoms]
    for atom in out:
        if _partial_occupancy_value(atom) >= 0.999:
            continue
        dg = _normal_disorder_tag(atom.get("dg"))
        da = _normal_disorder_tag(atom.get("da"))
        majors = major_by_assembly.get(da)
        if not majors or dg in (".", "?", ""):
            continue
        atom["_is_minor"] = dg not in majors
        # Synthetic fields are intentionally assembly-local: bond
        # conflict checks should reject A/major ↔ A/minor, but should
        # not make independent disorder assemblies conflict globally.
        atom["_mv_auto_disorder_assembly"] = da
        atom["_mv_auto_disorder_group"] = dg
    return out


def _tag_shelx_occupancy_disorder(raw_atoms, cif_path: str, M):
    """If ``raw_atoms`` contains SHELX-style occupancy disorder,
    consult :func:`molcrys_kit.analysis.disorder.\
generate_ordered_replicas_from_disordered_sites` for the optimal
    rotamer choice and tag every non-chosen disorder image with
    ``_is_minor=True``.

    The renderer continues to draw every atom; minor images are just
    faded to ``disorder_alpha=0.22`` and the fragment-table builder
    drops them so the stoichiometry / coordination polyhedron analysis
    sees a single chemically sensible structure.

    MolCrysKit exposes the selected source-site indices via
    ``return_kept_indices=True``; MatterVis only mirrors that selection
    onto its raw atom dicts for rendering.

    All steps are wrapped in a single ``try`` block: if MolCrysKit
    can't resolve the disorder for any reason (missing dependency,
    parser error, CIF rejected by ``scan_cif_disorder``) we leave
    ``raw_atoms`` untouched. Blank partial occupancy alone is not enough
    evidence to mark atoms minor because ordered special-position sites
    use the same CIF shape.
    """
    if not _has_shelx_occupancy_disorder(raw_atoms):
        return raw_atoms

    simple_major_groups = _simple_explicit_assembly_major_groups(raw_atoms)
    if simple_major_groups:
        return _tag_simple_explicit_assembly_disorder(raw_atoms, simple_major_groups)

    try:
        try:
            from molcrys_kit.analysis.disorder import (
                generate_ordered_replicas_from_disordered_sites,
            )

            try:
                replicas = generate_ordered_replicas_from_disordered_sites(
                    cif_path, method="optimal", return_kept_indices=True
                )
            except Exception:
                # Ionic type symbols (Fe3+, O2-, …) are normalised by the
                # CIF adapter through MolCrysKit's public in-memory reader.
                # Retry disorder resolution with that canonical crystal so a
                # charged CIF does not silently lose its disorder metadata.
                from ..structure.cif_parse import load_cif

                parsed = load_cif(cif_path)
                replicas = generate_ordered_replicas_from_disordered_sites(
                    method="optimal",
                    crystal=parsed.crystal,
                    return_kept_indices=True,
                )
        except Exception:
            return raw_atoms
        if not replicas:
            return raw_atoms
        first = replicas[0]
        if not isinstance(first, tuple) or len(first) != 2:
            return raw_atoms
        _crystal, kept_indices = first
        # Bridge MCK's DisorderInfo index space → raw_atoms via geometric
        # matching.  The two CIF parsers expand symmetry independently,
        # so their index spaces are NOT aligned (see disorder_index.py).
        from ..structure.disorder_index import map_mck_indices_to_raw

        idx_map = map_mck_indices_to_raw(cif_path, raw_atoms, kept_indices)
        kept_raw = set(idx_map.values())

        if not kept_raw and kept_indices:
            import logging

            logging.getLogger(__name__).error(
                "map_mck_indices_to_raw returned empty for %s "
                "(%d kept_indices could not be matched to raw_atoms). "
                "Disorder tagging will be skipped to avoid marking all "
                "atoms as minor.",
                cif_path,
                len(kept_indices),
            )
            return raw_atoms

        out = [dict(atom) for atom in raw_atoms]

        disordered_idx: list[int] = []
        occupancy_only_idx = _occupancy_only_disorder_indices(out)
        explicit_assembly_idx = _explicit_assembly_disorder_indices(out)
        explicit_group_idx = _explicit_group_disorder_indices(out)
        for idx, atom in enumerate(out):
            occ = _partial_occupancy_value(atom)
            if occ >= 0.999 or "_is_minor" in atom:
                continue
            dg = str(atom.get("dg") or ".").strip()
            if (
                idx in occupancy_only_idx
                or idx in explicit_assembly_idx
                or idx in explicit_group_idx
                or (dg.startswith("-") and dg not in ("-",))
            ):
                disordered_idx.append(idx)

        if not disordered_idx:
            return out

        # --- Validate MCK's choice against crystallographic occupancy ---
        # For explicit-assembly disorder (CIFs with non-blank
        # _atom_site_disorder_assembly + _atom_site_disorder_group),
        # the "kept" (major) set MUST correspond to the higher-
        # occupancy group — this is a hard crystallographic invariant.
        # MCK's 'optimal' method uses structural/connectivity criteria
        # that can contradict occupancy (observed on GAGCIF01: it kept
        # occ=0.474 B over occ=0.526 A, rendering both cations as
        # semi-transparent).  When the choice is inverted, correct it
        # and log a warning.
        if explicit_assembly_idx:
            kept_occs = [
                _partial_occupancy_value(out[i])
                for i in disordered_idx
                if i in kept_raw and i in explicit_assembly_idx
            ]
            disc_occs = [
                _partial_occupancy_value(out[i])
                for i in disordered_idx
                if i not in kept_raw and i in explicit_assembly_idx
            ]
            if kept_occs and disc_occs:
                avg_kept = sum(kept_occs) / len(kept_occs)
                avg_disc = sum(disc_occs) / len(disc_occs)
                if avg_kept < avg_disc - 1e-4:
                    import logging

                    logging.getLogger(__name__).warning(
                        "MCK optimal-replica chose lower-occupancy group "
                        "(avg_kept=%.4f < avg_disc=%.4f) for %s; "
                        "overriding with occupancy-based assignment.",
                        avg_kept,
                        avg_disc,
                        cif_path,
                    )
                    # Flip: for explicit-assembly atoms use occupancy
                    # to determine major/minor directly.
                    kept_raw = {
                        i
                        for i in disordered_idx
                        if i not in kept_raw and i in explicit_assembly_idx
                    } | (kept_raw - explicit_assembly_idx)

        # Explicit major / minor labels on every disordered atom: a
        # chosen atom must have ``_is_minor=False`` set (NOT just
        # absent) because ``_is_minor_atom`` falls through to the
        # ``dg.startswith("-")`` SHELX-PART heuristic when the flag is
        # missing -- that heuristic would otherwise re-classify the
        # chosen N3 / N2 atoms as minor and the bond graph would lose
        # the en cation entirely (the "C2N2 missing" SY bug).
        for idx in disordered_idx:
            if idx in kept_raw:
                out[idx]["_is_minor"] = False
                out[idx]["_mv_auto_disorder_assembly"] = "mv_auto"
                out[idx]["_mv_auto_disorder_group"] = "1"
            else:
                out[idx]["_is_minor"] = True
                out[idx]["_mv_auto_disorder_assembly"] = "mv_auto"
                out[idx]["_mv_auto_disorder_group"] = "2"
        return out
    except Exception:
        return raw_atoms
