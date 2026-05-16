#!/usr/bin/env python3
"""
ORTEP-style crystal structure figures for SY, PEP, MPEP, HPEP.
Nature-quality figure: Axes3D for correct depth ordering, no cross-disorder bonds,
thick two-color bonds, matte atoms, smart label placement.
"""

import argparse
import re
import gemmi
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Ellipse
from matplotlib.lines import Line2D
from mpl_toolkits.mplot3d import Axes3D          # noqa: F401 – registers projection
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import os
import math
from collections import OrderedDict
from types import SimpleNamespace

try:
    from ..disorder import atom_is_minor
except ImportError:  # pragma: no cover - allows direct script execution
    from crystal_viewer.disorder import atom_is_minor  # type: ignore

try:
    from .crystal_scene import (
        build_default_scenes,
        build_scene_from_atoms,
        default_preset,
        load_preset,
        save_preset,
    )
except ImportError:  # pragma: no cover - allows direct script execution
    from crystal_scene import (  # type: ignore
        build_default_scenes,
        build_scene_from_atoms,
        default_preset,
        load_preset,
        save_preset,
    )

# ── Element colours — Nature-style muted palette ────────────────────────────
# Inspired by CCDC Mercury / Nature structural biology figures:
# low saturation, print-safe, distinguishable in greyscale
ELEM_COLOR = {
    'C':  "#5E5E5E",   # dark charcoal gray
    'H':  "#DDDDDD",   # light gray
    'N':  "#2C61AF",   # muted steel blue
    'O':  "#B85060",   # muted brick red
    'Cl': "#218E6A",   # muted sage green
    'Cu': "#B87333",
    'Fe': "#B7410E",
    'Ni': "#4C8C4A",
    'Co': "#3F5FBF",
    'Zn': "#7D80B8",
    'default': '#808080',
}
ELEM_COLOR_LIGHT = {
    'C':  '#888888',   # medium gray (minor disorder)
    'H':  '#D8D8D8',
    'N':  '#8FADD4',   # lighter steel blue
    'O':  '#D48A88',   # lighter brick red
    'Cl': '#7DB88A',   # lighter sage green
    'Cu': '#D19A66',
    'Fe': '#D07A55',
    'Ni': '#82B57F',
    'Co': '#7F93D1',
    'Zn': '#A6A8D0',
    'default': '#B0B0B0',
}
# Atom display radii (Å) — used when no ADP available
ATOM_RADIUS = {'C': 0.18, 'N': 0.18, 'O': 0.17, 'Cl': 0.24, 'H': 0.08, 'Cu': 0.22, 'Fe': 0.22, 'Ni': 0.22, 'Co': 0.22, 'Zn': 0.22, 'default': 0.18}
COV_RADIUS   = {'C': 0.77, 'H': 0.31, 'N': 0.75, 'O': 0.73, 'Cl': 0.99, 'Cu': 1.32, 'Fe': 1.24, 'Ni': 1.21, 'Co': 1.26, 'Zn': 1.22}

def elem_color(s):       return ELEM_COLOR.get(s, ELEM_COLOR['default'])
def elem_color_light(s): return ELEM_COLOR_LIGHT.get(s, ELEM_COLOR_LIGHT['default'])
def atom_r(s):           return ATOM_RADIUS.get(s, ATOM_RADIUS['default'])
def cov_r(s):            return COV_RADIUS.get(s, 0.80)

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16)/255.0 for i in (0, 2, 4))

def hex_to_rgba(h, alpha=1.0):
    r, g, b = hex_to_rgb(h)
    return (r, g, b, alpha)

# ── Orthogonalisation matrix ────────────────────────────────────────────────
def ortho_matrix(cell):
    a, b, c = cell.a, cell.b, cell.c
    al = np.radians(cell.alpha); be = np.radians(cell.beta); ga = np.radians(cell.gamma)
    cos_al, cos_be, cos_ga = np.cos(al), np.cos(be), np.cos(ga)
    sin_ga = np.sin(ga); vol = cell.volume
    M = np.array([
        [a, b*cos_ga, c*cos_be],
        [0, b*sin_ga, c*(cos_al - cos_be*cos_ga)/sin_ga],
        [0, 0,        vol/(a*b*sin_ga)]
    ])
    N = M / np.array([a, b, c])
    return M, N

def _wrap_frac01(frac):
    frac = np.array(frac, dtype=float)
    return frac - np.floor(frac)

def nearest_lattice_shift_frac(delta_frac, M, search_radius=1):
    delta_frac = np.array(delta_frac, dtype=float)
    best_shift = np.zeros(3)
    best_dist = np.inf
    for na in range(-search_radius, search_radius + 1):
        for nb in range(-search_radius, search_radius + 1):
            for nc in range(-search_radius, search_radius + 1):
                shift = np.array([na, nb, nc], dtype=float)
                dist = np.linalg.norm(M @ (delta_frac - shift))
                if dist < best_dist:
                    best_dist = dist
                    best_shift = shift
    return best_shift

def bond_vector_mic(ai, aj, M, search_radius=1):
    delta_frac = np.array(aj['frac'], dtype=float) - np.array(ai['frac'], dtype=float)
    shift = nearest_lattice_shift_frac(delta_frac, M, search_radius=search_radius)
    delta_frac_mic = delta_frac - shift
    delta_cart = M @ delta_frac_mic
    return delta_cart, shift

def _nearest_pbc_cart(ref_cart, pos_cart, cell):
    ref = gemmi.Position(float(ref_cart[0]), float(ref_cart[1]), float(ref_cart[2]))
    pos = gemmi.Position(float(pos_cart[0]), float(pos_cart[1]), float(pos_cart[2]))
    nearest = cell.find_nearest_pbc_position(ref, pos, 0)
    return np.array([nearest.x, nearest.y, nearest.z], dtype=float)

# ── View rotation ───────────────────────────────────────────────────────────
def view_rotation(view_vec, up_vec=None):
    z = np.array(view_vec, dtype=float); z /= np.linalg.norm(z)
    if up_vec is None:
        up = np.array([0.,1.,0.]) if abs(z[1]) < 0.9 else np.array([0.,0.,1.])
    else:
        up = np.array(up_vec, dtype=float)
    x = np.cross(up, z)
    if np.linalg.norm(x) < 1e-6:
        up = np.array([1.,0.,0.]); x = np.cross(up, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x); y /= np.linalg.norm(y)
    return np.array([x, y, z])

# ── Convert view-direction vector to Axes3D elev/azim ───────────────────────
def view_vec_to_elev_azim(view_vec):
    """
    Convert a 3D Cartesian view direction vector to matplotlib Axes3D
    elevation and azimuth angles (degrees).
    view_vec points FROM the scene TOWARD the viewer.
    """
    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    # elev: angle above xy-plane
    elev = np.degrees(np.arcsin(np.clip(v[2], -1, 1)))
    # azim: angle in xy-plane from x-axis
    azim = np.degrees(np.arctan2(v[1], v[0]))
    return elev, azim

# ── Parse CIF ───────────────────────────────────────────────────────────────
def parse_asu(path):
    doc = gemmi.cif.read(path)
    block = doc.sole_block()

    def fv(tag):
        v = block.find_value(tag)
        return float(gemmi.cif.as_number(v)) if v else None

    a=fv('_cell_length_a'); b=fv('_cell_length_b'); c=fv('_cell_length_c')
    al=fv('_cell_angle_alpha') or 90.; be=fv('_cell_angle_beta') or 90.; ga=fv('_cell_angle_gamma') or 90.
    cell = gemmi.UnitCell(a, b, c, al, be, ga)
    M, N = ortho_matrix(cell)

    symops = []
    for tag in ['_space_group_symop_operation_xyz', '_symmetry_equiv_pos_as_xyz']:
        tbl = block.find([tag])
        if tbl:
            for row in tbl:
                try: symops.append(gemmi.Op(row[0].strip().strip("'")))
                except: pass
            break
    if not symops:
        symops = [gemmi.Op('x,y,z')]
    if len(symops) == 1:
        sg = None
        it_value = block.find_value('_space_group_IT_number') or block.find_value('_symmetry_Int_Tables_number')
        if it_value:
            try:
                sg = gemmi.find_spacegroup_by_number(int(gemmi.cif.as_number(it_value)))
            except Exception:
                sg = None
        if sg is None:
            for tag in ['_space_group_name_H-M_alt', '_symmetry_space_group_name_H-M', '_space_group_name_H-M']:
                name = block.find_value(tag)
                if not name:
                    continue
                try:
                    cleaned = str(name).strip().strip("'").strip('"')
                    if cleaned and cleaned.upper().replace(" ", "") not in {'P1', 'P-1'}:
                        sg = gemmi.SpaceGroup(cleaned)
                        break
                except Exception:
                    continue
        if sg is not None and sg.number > 1:
            try:
                expanded_ops = list(sg.operations())
                if len(expanded_ops) > 1:
                    symops = expanded_ops
            except Exception:
                pass

    bond_partners = {}
    bond_lengths = {}
    bond_tbl = block.find([
        '_geom_bond_atom_site_label_1',
        '_geom_bond_atom_site_label_2',
        '_geom_bond_distance',
    ])
    for row in bond_tbl:
        a = row[0].strip()
        b = row[1].strip()
        if a in ('', '.', '?') or b in ('', '.', '?'):
            continue
        try:
            dist = float(gemmi.cif.as_number(row[2]))
        except Exception:
            dist = None
        bond_partners.setdefault(a, set()).add(b)
        bond_partners.setdefault(b, set()).add(a)
        if dist is not None:
            bond_lengths.setdefault(a, {}).setdefault(b, []).append(dist)
            bond_lengths.setdefault(b, {}).setdefault(a, []).append(dist)

    # Read each `_atom_site_*` column independently so we don't fail when the
    # CIF omits optional tags (e.g. Materials-Studio exports that drop
    # `_atom_site_disorder_group` / `_atom_site_disorder_assembly`).
    def _column(tag, *, required=False, default='.'):
        values = list(block.find_loop(tag))
        if values:
            return values
        if required:
            raise ValueError(f"CIF is missing required tag: {tag}")
        return None

    labels = _column('_atom_site_label', required=True)
    types  = _column('_atom_site_type_symbol')
    xs     = _column('_atom_site_fract_x', required=True)
    ys     = _column('_atom_site_fract_y', required=True)
    zs     = _column('_atom_site_fract_z', required=True)
    occs   = _column('_atom_site_occupancy')
    uisos  = _column('_atom_site_U_iso_or_equiv')
    dgs    = _column('_atom_site_disorder_group')
    das    = _column('_atom_site_disorder_assembly')

    n_rows = len(labels)
    if types is None:
        types = [re.sub(r'\d', '', label) or 'C' for label in labels]
    asu_atoms = []
    for i in range(n_rows):
        label = labels[i]
        elem = (types[i] if i < len(types) else 'C').strip().capitalize()
        try:
            x = float(gemmi.cif.as_number(xs[i]))
            y = float(gemmi.cif.as_number(ys[i]))
            z = float(gemmi.cif.as_number(zs[i]))
        except Exception:
            continue
        try:
            occ = float(gemmi.cif.as_number(occs[i])) if occs else 1.0
        except Exception:
            occ = 1.0
        try:
            uiso = float(gemmi.cif.as_number(uisos[i])) if uisos else 0.04
        except Exception:
            uiso = 0.04
        dg = (dgs[i] if dgs else '.').strip()
        da = (das[i] if das else '.').strip()
        asu_atoms.append({'label': label, 'elem': elem,
                          'frac': np.array([x,y,z]),
                          'occ': occ, 'uiso': uiso,
                          'dg': dg, 'da': da,
                          '_bond_partners': tuple(sorted(bond_partners.get(label, ()))),
                          '_bond_lengths': {
                              partner: tuple(lengths)
                              for partner, lengths in bond_lengths.get(label, {}).items()
                          },
                          '_has_bond_table': bool(bond_partners)})

    aniso_tbl = block.find(['_atom_site_aniso_label',
                            '_atom_site_aniso_U_11',
                            '_atom_site_aniso_U_22',
                            '_atom_site_aniso_U_33',
                            '_atom_site_aniso_U_12',
                            '_atom_site_aniso_U_13',
                            '_atom_site_aniso_U_23'])
    aniso = {}
    for row in aniso_tbl:
        try:
            u = np.array([[float(gemmi.cif.as_number(row[1])),
                           float(gemmi.cif.as_number(row[4])),
                           float(gemmi.cif.as_number(row[5]))],
                          [float(gemmi.cif.as_number(row[4])),
                           float(gemmi.cif.as_number(row[2])),
                           float(gemmi.cif.as_number(row[6]))],
                          [float(gemmi.cif.as_number(row[5])),
                           float(gemmi.cif.as_number(row[6])),
                           float(gemmi.cif.as_number(row[3]))]])
            aniso[row[0]] = u
        except: pass

    atoms = []
    seen_cart = []

    for asu_at in asu_atoms:
        frac0 = asu_at['frac']
        # Always expand by every symmetry operation, regardless of
        # occupancy. The previous "only the first symop for disordered
        # atoms" rule meant a partial-occupancy carbon (e.g. PEP's
        # C8/C8A at 0.5 each) ended up at a single asymmetric-unit
        # position while its full-occupancy nitrogen neighbour expanded
        # to all 8 unit-cell sites. The 7 nitrogen images then had no
        # carbon neighbour anywhere in the structure and surfaced as
        # bogus lone-N "fragments" in the topology UI. Special-position
        # overlaps are still handled by the ``seen_cart`` dedup below.
        ops = symops
        for symop_index, op in enumerate(ops):
            frac_new = np.array(op.apply_to_xyz(list(frac0)), dtype=float)
            frac_basic = _wrap_frac01(frac_new)
            cart_new = M @ frac_basic

            # Deduplicate only symmetry images of the *same crystallographic
            # label*. Different labels can legitimately sit very close on
            # special positions or disorder-related sites; dropping them here
            # silently deletes raw CIF sites and can break rings/bond tables.
            dup = any(
                prev_label == asu_at['label'] and np.linalg.norm(cart_new - sc) < 0.15
                for prev_label, sc in seen_cart
            )
            if dup:
                continue
            seen_cart.append((asu_at['label'], cart_new))

            U_cart = None
            if asu_at['label'] in aniso:
                U_cif = aniso[asu_at['label']]
                U_cart_asu = N @ U_cif @ N.T
                r_int = op.rot
                r_mat = np.array(r_int, dtype=float).reshape(3, 3) / 24.0
                try:
                    M_inv = np.linalg.inv(M)
                    R_cart = M @ r_mat @ M_inv
                    U_cart = R_cart @ U_cart_asu @ R_cart.T
                except:
                    U_cart = U_cart_asu

            atoms.append({'label': asu_at['label'], 'elem': asu_at['elem'],
                          'frac': frac_basic, 'cart': cart_new.copy(),
                          'occ': asu_at['occ'], 'uiso': asu_at['uiso'],
                          'dg': asu_at['dg'], 'da': asu_at['da'],
                          'U': U_cart,
                          '_asym_label': asu_at['label'],
                          '_symop_index': int(symop_index),
                          '_raw_instance_id': f"{asu_at['label']}@sym{symop_index}",
                          '_bond_partners': asu_at.get('_bond_partners', ()),
                          '_bond_lengths': asu_at.get('_bond_lengths', {}),
                          '_has_bond_table': asu_at.get('_has_bond_table', False)})

    # Reassemble fragmented ClO₄ groups
    a_vec = M[:, 0]; b_vec = M[:, 1]; c_vec = M[:, 2]
    cl_atoms = [at for at in atoms if at['elem'] == 'Cl']
    for at in atoms:
        if at['elem'] != 'O':
            continue
        bonded = any(np.linalg.norm(at['cart'] - cl['cart']) < 1.70
                     for cl in cl_atoms)
        if bonded:
            continue
        best_dist = np.inf
        best_shift_frac = np.zeros(3)
        for cl in cl_atoms:
            delta_cart, shift = bond_vector_mic(cl, at, M, search_radius=1)
            d = np.linalg.norm(delta_cart)
            if d < best_dist:
                best_dist = d
                best_shift_frac = -shift
        if best_dist < 1.70:
            shift_cart = M @ best_shift_frac
            at['frac'] = at['frac'] + best_shift_frac
            at['cart'] = at['cart'] + shift_cart

    return atoms, cell, M

# ── Disorder helpers ────────────────────────────────────────────────────────
def _has_disorder_metadata(at):
    dg = at.get('dg', '').strip()
    da = at.get('da', '').strip()
    occ = float(at.get('occ', 1.0))
    return dg not in ('.', '?', '') or da not in ('.', '?', '') or occ < 0.999


def is_major(at):
    if '_is_major' in at:
        return bool(at['_is_major'])
    if not _has_disorder_metadata(at):
        return True
    return not is_minor(at)

def is_minor(at):
    # Loader provenance is the single source of truth for render fading.
    return atom_is_minor(at)

def disorder_alpha(at):
    if is_minor(at):
        return 0.22   # minor disorder: clearly faded behind major atoms
    return 1.0

def _disorder_group_id(at):
    """Return a canonical disorder group identifier for conflict checking."""
    synthetic_dg = str(at.get('_mv_auto_disorder_group') or '').strip()
    if synthetic_dg not in ('', '.', '?'):
        synthetic_da = str(at.get('_mv_auto_disorder_assembly') or 'mv_auto').strip()
        if synthetic_da in ('', '.', '?'):
            synthetic_da = 'mv_auto'
        return (synthetic_da, synthetic_dg)
    dg = at['dg'].strip()
    da = at['da'].strip()
    if dg in ('.', '?', ''):
        return None
    return (da, dg)

def bonds_conflict(ai, aj):
    """
    Return True if ai and aj are in conflicting disorder groups
    (same assembly, different group — like PART 1 vs PART 2 in SHELX).
    """
    gi = _disorder_group_id(ai)
    gj = _disorder_group_id(aj)
    if gi is None or gj is None:
        return False
    da_i, dg_i = gi
    da_j, dg_j = gj
    if da_i in ('.', '?', '') and da_j in ('.', '?', ''):
        return dg_i != dg_j
    return da_i == da_j and dg_i != dg_j

def _bond_cutoff(ai, aj):
    ei, ej = ai['elem'], aj['elem']
    if ei == 'H' and ej == 'H':
        return None
    if set([ei, ej]) == {'Cl', 'O'}:
        return 1.62
    if 'H' in [ei, ej]:
        return 1.15
    return cov_r(ei) + cov_r(ej) + 0.42


def _bond_allowed_by_table(ai, aj):
    partners_i = ai.get('_bond_partners', ())
    partners_j = aj.get('_bond_partners', ())
    has_table = bool(ai.get('_has_bond_table')) or bool(aj.get('_has_bond_table'))
    if not has_table:
        return True
    if partners_i and aj['label'] in partners_i:
        return True
    if partners_j and ai['label'] in partners_j:
        return True
    return False


def _bond_matches_table_distance(ai, aj, distance):
    has_table = bool(ai.get('_has_bond_table')) or bool(aj.get('_has_bond_table'))
    if not has_table:
        return True
    candidates = []
    for ref in ai.get('_bond_lengths', {}).get(aj['label'], ()):
        candidates.append(float(ref))
    for ref in aj.get('_bond_lengths', {}).get(ai['label'], ()):
        candidates.append(float(ref))
    if not candidates:
        return True
    tolerance = 0.18 if 'H' in (ai['elem'], aj['elem']) else 0.22
    return min(abs(distance - ref) for ref in candidates) <= tolerance

def _has_bond_table_atom(atom):
    return bool(atom.get('_has_bond_table'))


def _prune_duplicate_label_bond_candidates(atoms, candidates, tol=0.005):
    """Remove cross-bonds between duplicate disorder/symmetry alternatives.

    CIF bond tables describe site labels, not every symmetry-expanded copy. If
    a scene contains two alternatives with the same label (typical for PART or
    mirror-generated disorder), a naive label-table check allows every C2 copy
    to bond to every F4 copy. Keep only the nearest copy for each
    atom->partner-label relation. This is deliberately in the MatterVis bond
    layer so publication scripts cannot accidentally draw cross-disorder bonds.
    """
    if not candidates:
        return []

    label_counts = {}
    for atom in atoms:
        label = atom.get('label')
        label_counts[label] = label_counts.get(label, 0) + 1

    best = {}
    for i, j, d in candidates:
        ai, aj = atoms[i], atoms[j]
        duplicated = label_counts.get(ai.get('label'), 0) > 1 or label_counts.get(aj.get('label'), 0) > 1
        table_guided = _has_bond_table_atom(ai) or _has_bond_table_atom(aj)
        if not (duplicated and table_guided):
            continue
        for src, dst in ((i, j), (j, i)):
            key = (src, atoms[dst].get('label'))
            best[key] = min(float(d), best.get(key, np.inf))

    if not best:
        return candidates

    pruned = []
    for i, j, d in candidates:
        ai, aj = atoms[i], atoms[j]
        duplicated = label_counts.get(ai.get('label'), 0) > 1 or label_counts.get(aj.get('label'), 0) > 1
        table_guided = _has_bond_table_atom(ai) or _has_bond_table_atom(aj)
        if duplicated and table_guided:
            if float(d) > best.get((i, aj.get('label')), np.inf) + tol:
                continue
            if float(d) > best.get((j, ai.get('label')), np.inf) + tol:
                continue
        pruned.append((i, j, d))
    return pruned


# ── Bond finding ────────────────────────────────────────────────────────────
_BOND_KDTREE_THRESHOLD = 64
_BOND_MAX_CUTOFF = 5.0  # Å — wide enough for any covalent pair the table can return.


def _bond_candidate_pairs(atoms, M, cell):
    """Yield ``(i, j)`` index pairs with ``i < j`` whose Cartesian
    distance (or, when ``cell is not None``, **PBC** Cartesian distance)
    is plausibly within bond range.

    For ``len(atoms) >= _BOND_KDTREE_THRESHOLD`` we use
    ``cKDTree.query_pairs`` to prune the O(N^2) python loop down to
    O(N * neighbours). For smaller scenes the python loop is faster
    than constructing a KDTree, so we keep the legacy enumeration.

    PBC handling: when ``cell is not None`` and ``M`` is provided, the
    atom set is expanded with one image-replica per neighbour cell
    (3^3 - 1 = 26 ghosts per atom) before the KDTree query. Pairs that
    fall within the cutoff are then mapped back to the home index. This
    is necessary because a ring that crosses the cell boundary has
    bonds whose **raw cart** length spans the cell (8+ Å) but whose
    PBC-image length is normal covalent. Without ghost replication,
    ``_unwrapped_atoms_from_atoms`` cannot reassemble the ring and the
    user sees fragmented organic cations -- regression observed on the
    MPEP structure (P2_1/c, monoclinic) after the v1 KDTree pre-filter
    landed.
    """
    n = len(atoms)
    if n < _BOND_KDTREE_THRESHOLD:
        for i in range(n):
            for j in range(i + 1, n):
                yield (i, j)
        return
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        for i in range(n):
            for j in range(i + 1, n):
                yield (i, j)
        return
    coords = np.asarray([a['cart'] for a in atoms], dtype=float)

    # No PBC requested -> plain non-periodic KDTree on raw cart coords.
    if cell is None or M is None:
        tree = cKDTree(coords)
        pairs = tree.query_pairs(r=_BOND_MAX_CUTOFF, output_type='ndarray')
        if pairs.size == 0:
            return
        for i, j in pairs.tolist():
            yield (int(i), int(j)) if i < j else (int(j), int(i))
        return

    # PBC path: expand the atom set with 26 image-replicas so that
    # cross-cell bonds become local in cart space.
    M_arr = np.asarray(M, dtype=float)
    a_vec = M_arr[:, 0]
    b_vec = M_arr[:, 1]
    c_vec = M_arr[:, 2]
    coord_chunks = [coords]
    orig_idx_chunks = [np.arange(n, dtype=int)]
    for da in (-1, 0, 1):
        for db in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if da == 0 and db == 0 and dc == 0:
                    continue
                offset = da * a_vec + db * b_vec + dc * c_vec
                coord_chunks.append(coords + offset)
                orig_idx_chunks.append(np.arange(n, dtype=int))
    all_coords = np.vstack(coord_chunks)
    all_orig = np.concatenate(orig_idx_chunks)

    tree = cKDTree(all_coords)
    pairs = tree.query_pairs(r=_BOND_MAX_CUTOFF, output_type='ndarray')
    if pairs.size == 0:
        return
    seen: set[tuple[int, int]] = set()
    for i, j in pairs.tolist():
        oi = int(all_orig[i])
        oj = int(all_orig[j])
        if oi == oj:
            continue  # ghost-of-self at zero distance is not a bond candidate
        a_idx, b_idx = (oi, oj) if oi < oj else (oj, oi)
        if (a_idx, b_idx) in seen:
            continue
        seen.add((a_idx, b_idx))
        yield (a_idx, b_idx)


def find_bonds(atoms, M=None, cell=None):
    """Find bonds, excluding cross-disorder-group and cross-alternative bonds.

    For large atom counts (~64+ atoms, see ``_BOND_KDTREE_THRESHOLD``)
    the candidate set is pre-filtered with a Cartesian KDTree on
    ``cart`` coordinates. The slow per-pair table check then only runs
    on plausible neighbours. This drops the cost from O(N^2) to
    O(N * k) where k ~ 10-20 covalent neighbours per atom -- the
    difference between 1 second and 1 minute on a 1500-atom supercell.
    """
    candidates = []
    for i, j in _bond_candidate_pairs(atoms, M=M, cell=cell):
        ai = atoms[i]
        aj = atoms[j]
        if not _bond_allowed_by_table(ai, aj):
            continue
        if bonds_conflict(ai, aj):
            continue
        cutoff = _bond_cutoff(ai, aj)
        if cutoff is None:
            continue
        if cell is not None:
            near = _nearest_pbc_cart(ai['cart'], aj['cart'], cell)
            d = np.linalg.norm(near - ai['cart'])
        elif M is None:
            d = np.linalg.norm(ai['cart'] - aj['cart'])
        else:
            d = np.linalg.norm(bond_vector_mic(ai, aj, M, search_radius=1)[0])
        if not _bond_matches_table_distance(ai, aj, d):
            continue
        if d < cutoff:
            candidates.append((i, j, float(d)))

    return [(i, j) for i, j, _ in _prune_duplicate_label_bond_candidates(atoms, candidates)]

# ── Cluster atoms ────────────────────────────────────────────────────────────
def cluster_atoms(atoms, M=None, cell=None, bonds=None):
    n = len(atoms)
    parent = list(range(n))
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(x, y):
        px, py = find(x), find(y)
        if px != py: parent[px] = py
    if bonds is None:
        bonds = find_bonds(atoms, M=M, cell=cell)
    for i, j in bonds:
        union(i, j)
    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    return clusters

# ── PBC nearest image helper ─────────────────────────────────────────────────
def _pbc_nearest(centroid, ref_point, a_vec, b_vec, c_vec):
    best_dist = np.inf
    best_offset = np.zeros(3)
    for na in range(-2, 3):
        for nb in range(-2, 3):
            for nc in range(-2, 3):
                offset = na*a_vec + nb*b_vec + nc*c_vec
                d = np.linalg.norm(centroid + offset - ref_point)
                if d < best_dist:
                    best_dist = d
                    best_offset = offset
    return best_dist, best_offset

def _translate_cluster(atoms, idxs, offset):
    if np.linalg.norm(offset) < 1e-6:
        return
    for i in idxs:
        atoms[i] = dict(atoms[i])
        atoms[i]['cart'] = atoms[i]['cart'] + offset

def _translate_cluster_frac(atoms, idxs, shift_frac, M):
    shift_frac = np.array(shift_frac, dtype=float)
    if np.linalg.norm(shift_frac) < 1e-9:
        return
    shift_cart = M @ shift_frac
    for i in idxs:
        atoms[i] = dict(atoms[i])
        atoms[i]['frac'] = np.array(atoms[i]['frac'], dtype=float) + shift_frac
        atoms[i]['cart'] = atoms[i]['cart'] + shift_cart

def assemble_component_p1(atoms, idxs, bond_pairs, M):
    idxs = list(idxs)
    idx_set = set(idxs)
    adjacency = {i: [] for i in idxs}
    for i, j in bond_pairs:
        if i in idx_set and j in idx_set:
            adjacency[i].append(j)
            adjacency[j].append(i)
    shifts = {idxs[0]: np.zeros(3)}
    queue = [idxs[0]]
    while queue:
        i = queue.pop(0)
        for j in adjacency.get(i, []):
            delta_frac = np.array(atoms[j]['frac'], dtype=float) - np.array(atoms[i]['frac'], dtype=float)
            nearest_shift = nearest_lattice_shift_frac(delta_frac, M, search_radius=1)
            proposed = shifts[i] - nearest_shift
            if j not in shifts:
                shifts[j] = proposed
                queue.append(j)
    atoms_out = [dict(at) for at in atoms]
    for i in idxs:
        shift_frac = shifts.get(i, np.zeros(3))
        atoms_out[i]['frac'] = np.array(atoms[i]['frac'], dtype=float) + shift_frac
        atoms_out[i]['cart'] = M @ atoms_out[i]['frac']
    return atoms_out

def _cluster_attachment_cost(cluster_idxs, selected_idxs, atoms, M, shift_frac):
    shift_cart = M @ np.array(shift_frac, dtype=float)
    cluster_cart = np.array([atoms[i]['cart'] for i in cluster_idxs]) + shift_cart
    selected_cart = np.array([atoms[i]['cart'] for i in selected_idxs])
    if len(selected_cart) == 0:
        return 0.0
    dists = np.sqrt(((cluster_cart[:, None, :] - selected_cart[None, :, :]) ** 2).sum(axis=2)).ravel()
    nearest = np.sort(dists)
    k = nearest[:min(8, len(nearest))]
    overlap_pen = np.sum(np.clip(1.35 - nearest[:min(12, len(nearest))], 0.0, None) ** 2)
    return float(np.mean(k) + overlap_pen * 8.0)

def _best_cluster_shift_frac(cluster_idxs, selected_idxs, atoms, M, search_radius=2):
    best_cost = np.inf
    best_shift = np.zeros(3)
    for na in range(-search_radius, search_radius + 1):
        for nb in range(-search_radius, search_radius + 1):
            for nc in range(-search_radius, search_radius + 1):
                shift = np.array([na, nb, nc], dtype=float)
                cost = _cluster_attachment_cost(cluster_idxs, selected_idxs, atoms, M, shift)
                if cost < best_cost:
                    best_cost = cost
                    best_shift = shift
    return best_shift, best_cost

def _grow_local_environment(atoms, anchor_idxs, candidate_clusters, M, max_count):
    selected = list(anchor_idxs)
    remaining = list(candidate_clusters)
    chosen = []
    while remaining and len(chosen) < max_count:
        scored = []
        for root, idxs in remaining:
            shift_frac, cost = _best_cluster_shift_frac(idxs, selected, atoms, M, search_radius=2)
            scored.append((cost, root, idxs, shift_frac))
        scored.sort(key=lambda item: item[0])
        _, root, idxs, shift_frac = scored[0]
        _translate_cluster_frac(atoms, idxs, shift_frac, M)
        selected.extend(idxs)
        chosen.append((root, idxs))
        remaining = [(r, c) for r, c in remaining if r != root]
    return selected, chosen

# ── Select one formula unit ──────────────────────────────────────────────────
def select_formula_unit(atoms, M, cell):
    atoms = [dict(a) for a in atoms]
    bond_pairs = find_bonds(atoms, cell=cell)
    clusters = cluster_atoms(atoms, bonds=bond_pairs)
    for idxs in clusters.values():
        atoms = assemble_component_p1(atoms, idxs, bond_pairs, M)

    organic_clusters = {}
    anion_clusters = {}
    for root, idxs in clusters.items():
        elems = set(atoms[i]['elem'] for i in idxs if atoms[i]['elem'] != 'H')
        if 'Cl' in elems:
            anion_clusters[root] = idxs
        elif 'C' in elems or 'N' in elems:
            organic_clusters[root] = idxs

    if not organic_clusters:
        return atoms, list(range(len(atoms)))

    org_list = sorted(organic_clusters.items(),
                      key=lambda kv: len(kv[1]), reverse=True)
    anchor_root, anchor_idxs = org_list[0]
    anchor_size = len(anchor_idxs)
    anchor_labels = frozenset(atoms[i]['label'] for i in anchor_idxs)

    selected_org_idxs = list(anchor_idxs)

    if len(org_list) >= 2:
        preferred = []
        fallback = []
        for root, idxs in org_list[1:]:
            if len(idxs) < anchor_size * 0.35:
                continue
            clabels = frozenset(atoms[i]['label'] for i in idxs)
            item = (root, idxs)
            if clabels & anchor_labels:
                fallback.append(item)
            else:
                preferred.append(item)
        candidates = preferred if preferred else fallback
        if candidates:
            selected_org_idxs, chosen_org = _grow_local_environment(
                atoms, selected_org_idxs, candidates, M, max_count=1)

    selected_idxs = list(selected_org_idxs)
    anion_candidates = [(root, idxs) for root, idxs in anion_clusters.items() if len(idxs) >= 4]
    if len(anion_candidates) < 4:
        anion_candidates = list(anion_clusters.items())
    selected_idxs, _ = _grow_local_environment(
        atoms, selected_idxs, anion_candidates, M, max_count=min(4, len(anion_candidates)))

    return atoms, selected_idxs

# ── 3D ellipsoid polygon (billboard facing viewer) ──────────────────────────
def ellipsoid_3d_polygon(at, view_x, view_y, n_pts=48, size_scale=1.0):
    """
    Build a filled polygon (list of 3D vertices) representing the ORTEP
    50%-probability ellipsoid for atom `at`.

    The ellipse is drawn in the plane spanned by view_x and view_y
    (the screen x and y axes in 3D Cartesian space), centred at at['cart'].
    Semi-axes are derived from the projected 2D covariance.

    Returns: (verts3d, w_half, h_half, angle_rad)
      verts3d – (n_pts, 3) array of 3D polygon vertices
    """
    center = at['cart']
    elem   = at['elem']

    if at.get('U') is not None and elem != 'H':
        # Project U_cart onto the view plane
        U = at['U']
        # 2×3 projection matrix: rows are view_x, view_y
        P = np.array([view_x, view_y])   # (2,3)
        U2 = P @ U @ P.T                 # (2,2)
        U2 = (U2 + U2.T) / 2
        try:
            eigvals, eigvecs = np.linalg.eigh(U2)
            eigvals = np.abs(eigvals)
            scale = np.sqrt(1.3863)      # 50% probability
            a_ax = scale * np.sqrt(eigvals[0])
            b_ax = scale * np.sqrt(eigvals[1])
            a_ax = max(0.05, min(a_ax, 0.40))
            b_ax = max(0.05, min(b_ax, 0.40))
            a_ax *= size_scale
            b_ax *= size_scale
            # eigvecs[:,0] is the minor axis direction in 2D screen space
            e0 = eigvecs[:, 0]  # (2,)
            e1 = eigvecs[:, 1]  # (2,)
            # Convert 2D screen eigenvectors back to 3D Cartesian
            ax3d = e0[0]*view_x + e0[1]*view_y   # minor axis in 3D
            ay3d = e1[0]*view_x + e1[1]*view_y   # major axis in 3D
        except:
            a_ax = b_ax = 0.11
            ax3d = view_x; ay3d = view_y
    else:
        uiso = max(at.get('uiso', 0.04), 0.02)
        if elem == 'H':
            r = 0.07
        else:
            r = max(atom_r(elem)*0.8,
                    min(np.sqrt(1.3863 * uiso) * 0.65, atom_r(elem)*1.3))
        r *= size_scale
        a_ax = b_ax = r
        ax3d = view_x; ay3d = view_y

    t = np.linspace(0, 2*np.pi, n_pts, endpoint=False)
    # Ellipse parametric: center + a*cos(t)*ax3d + b*sin(t)*ay3d
    verts = center[np.newaxis, :] + \
            (a_ax * np.cos(t))[:, np.newaxis] * ax3d[np.newaxis, :] + \
            (b_ax * np.sin(t))[:, np.newaxis] * ay3d[np.newaxis, :]
    return verts, a_ax, b_ax

# ── Draw two-color bond in 3D ────────────────────────────────────────────────
BOND_LW = 6.6   # 3× the original 2.2

DEPTH_CUE = {
    'size_boost':   0.08,
    'fog_strength': 0.16,
    'lw_boost':     0.08,
}

def _depth_blend_white(rgb, t, fog_strength):
    """Blend rgb tuple toward white based on depth. t=1 nearest, t=0 farthest."""
    r, g, b = rgb
    f = fog_strength * (1.0 - t)
    return (r + (1.0 - r) * f,
            g + (1.0 - g) * f,
            b + (1.0 - b) * f)

def draw_bond_3d(ax, ai, aj, alpha_i, alpha_j, depth_t=None):
    xi, yi, zi = ai['cart']
    xj, yj, zj = aj['cart']
    xm, ym, zm = (xi+xj)/2, (yi+yj)/2, (zi+zj)/2
    ci = hex_to_rgb(elem_color(ai['elem']))
    cj = hex_to_rgb(elem_color(aj['elem']))
    lw = BOND_LW
    linestyle = '-'
    alpha = min(alpha_i, alpha_j)
    bond_is_minor = alpha < 0.999
    if depth_t is not None and not bond_is_minor:
        depth_t = float(np.clip(depth_t, 0.0, 1.0))
        lw *= 1 + DEPTH_CUE['lw_boost'] * (2*depth_t - 1)
        ci = _depth_blend_white(ci, depth_t, DEPTH_CUE['fog_strength'])
        cj = _depth_blend_white(cj, depth_t, DEPTH_CUE['fog_strength'])
    elif bond_is_minor:
        linestyle = (0, (1.2, 1.2))
        lw *= 0.95
    ax.plot([xi, xm], [yi, ym], [zi, zm], color=ci,
            lw=lw, solid_capstyle='round', alpha=alpha, linestyle=linestyle)
    ax.plot([xm, xj], [ym, yj], [zm, zj], color=cj,
            lw=lw, solid_capstyle='round', alpha=alpha, linestyle=linestyle)

# ── Draw matte atom in 3D (billboard ellipsoid) ──────────────────────────────
def draw_atom_3d(ax, at, view_x, view_y, alpha, depth_t=None):
    elem  = at['elem']
    color = elem_color(elem)
    color_light = elem_color_light(elem)
    minor = is_minor(at)
    size_s = 1.0
    face_rgb = hex_to_rgb(color)
    edge_rgb = hex_to_rgb('#222222')
    light_rgb = hex_to_rgb(color_light)
    if depth_t is not None and not minor:
        depth_t = float(np.clip(depth_t, 0.0, 1.0))
        size_s = 1 + DEPTH_CUE['size_boost'] * (2*depth_t - 1)
        face_rgb = _depth_blend_white(face_rgb, depth_t, DEPTH_CUE['fog_strength'])
        edge_rgb = _depth_blend_white(edge_rgb, depth_t, DEPTH_CUE['fog_strength'])
        light_rgb = _depth_blend_white(light_rgb, depth_t, DEPTH_CUE['fog_strength'])

    verts, a_ax, b_ax = ellipsoid_3d_polygon(at, view_x, view_y, size_scale=size_s)

    if minor:
        # Minor disorder keeps a dedicated visual language: no depth cueing,
        # no fill, and a clearer outline so it cannot be confused with depth.
        poly = Poly3DCollection([verts], zsort='min')
        poly.set_facecolor((1.0, 1.0, 1.0, 0.0))
        poly.set_edgecolor((*face_rgb, alpha))
        poly.set_linewidth(1.4)
        ax.add_collection3d(poly)
    else:
        # Major atom: filled polygon with matte highlight
        rgba_face = (*face_rgb, alpha)
        poly = Poly3DCollection([verts], zsort='min')
        poly.set_facecolor(rgba_face)
        poly.set_edgecolor((*edge_rgb, alpha))
        poly.set_linewidth(0.8)
        ax.add_collection3d(poly)

        # Matte highlight: smaller ellipse offset toward upper-left
        if elem != 'H':
            center = at['cart']
            hl_scale = 0.42
            hl_offset = (-a_ax * 0.10) * view_x + (b_ax * 0.10) * view_y
            hl_center = center + hl_offset
            t = np.linspace(0, 2*np.pi, 32, endpoint=False)
            hl_verts = hl_center[np.newaxis, :] + \
                       (a_ax * hl_scale * np.cos(t))[:, np.newaxis] * view_x[np.newaxis, :] + \
                       (b_ax * hl_scale * np.sin(t))[:, np.newaxis] * view_y[np.newaxis, :]
            rgba_hl = (*light_rgb, alpha * 0.50)
            hl_poly = Poly3DCollection([hl_verts], zsort='min')
            hl_poly.set_facecolor(rgba_hl)
            hl_poly.set_edgecolor('none')
            ax.add_collection3d(hl_poly)

# ── Smart label placement (screen-space, radial + collision avoidance) ───────
_LABEL_POS_CACHE: "OrderedDict[tuple, list]" = OrderedDict()
_LABEL_POS_CACHE_MAX = 64


def _label_pos_cache_key(label_atoms, view_x, view_y, base_offset, all_atoms):
    """Stable hash of inputs to ``_compute_label_positions``.

    Label placement is purely cosmetic and depends only on screen-space
    geometry: the rounded Cartesian positions of label atoms, the same
    for the (optional) ``all_atoms`` reference cloud, the view-plane
    basis vectors, and the requested radial offset. Style toggles and
    fragment colours don't affect the layout, so re-rendering the same
    scene with a different palette returns from cache.
    """
    label_bytes = np.round(
        np.array([atom["cart"] for atom in label_atoms], dtype=float), 4
    ).tobytes()
    if all_atoms is None:
        all_bytes = b""
    else:
        all_bytes = np.round(
            np.array([atom["cart"] for atom in all_atoms], dtype=float), 4
        ).tobytes()
    return (
        len(label_atoms),
        label_bytes,
        np.round(np.asarray(view_x, dtype=float), 5).tobytes(),
        np.round(np.asarray(view_y, dtype=float), 5).tobytes(),
        round(float(base_offset), 4),
        len(all_atoms) if all_atoms is not None else -1,
        all_bytes,
    )


def _compute_label_positions(label_atoms, view_x, view_y, base_offset=0.38,
                             all_atoms=None):
    """Compute 3D label positions for ``label_atoms`` using a vectorised
    force-directed layout.

    Step 1: Place each label radially outward from the structure centroid.
    Step 2: Iteratively push overlapping labels apart (Jacobi-style
            repulsion against every other label and against every atom's
            ellipsoid).

    Notes on the rewrite (2026-05): the original implementation looped in
    pure Python with ``math.sqrt`` per pair. For a 192-atom unit-cell
    scene that pinned ``build_scene_from_atoms`` at ~14 s of CPU, which
    is what the user feels as "every style change is slow" -- the slow
    cold scene build invalidates the cache often enough that the cost
    surfaces on every interaction. The numpy version below is a Jacobi
    sweep instead of Gauss-Seidel: forces from all neighbours are
    accumulated then applied at once. The visual result is functionally
    identical (label placement is purely cosmetic), and the cost drops
    from O(N_lab^2 + N_lab*N_atoms) python ops per iter to two numpy
    matmuls per iter.

    A content-hashed LRU on top kills the residual ~1 s/call on repeat
    transforms: changing the colour palette / opacity / display mode
    doesn't move atoms, so the layout is identical to the previous
    call and we can return the cached position list.
    """
    if not label_atoms:
        return []

    cache_key = _label_pos_cache_key(label_atoms, view_x, view_y, base_offset, all_atoms)
    cached = _LABEL_POS_CACHE.get(cache_key)
    if cached is not None:
        _LABEL_POS_CACHE.move_to_end(cache_key)
        return [pos.copy() for pos in cached]

    non_h = [a for a in label_atoms if a['elem'] != 'H']
    if not non_h:
        non_h = label_atoms
    if all_atoms is None:
        all_non_h_atoms = non_h
    else:
        all_non_h_atoms = [a for a in all_atoms if a.get('elem') != 'H']
        if not all_non_h_atoms:
            all_non_h_atoms = non_h

    view_x_arr = np.asarray(view_x, dtype=float)
    view_y_arr = np.asarray(view_y, dtype=float)

    # Pre-compute ellipse radii. We only need ``max(a_ax, b_ax)``, so the
    # full polygon vertices are wasted work; ``_label_atom_radius`` keeps
    # the same size convention but skips the ``np.linspace`` /
    # parametrisation legs.
    label_carts = np.array([a['cart'] for a in label_atoms], dtype=float)
    label_xy = np.column_stack(
        (label_carts @ view_x_arr, label_carts @ view_y_arr)
    )  # (N_lab, 2)
    ellipse_rs = np.array(
        [_label_atom_radius(at, view_x_arr, view_y_arr) for at in label_atoms],
        dtype=float,
    )

    non_h_carts = np.array([a['cart'] for a in non_h], dtype=float)
    cx = float((non_h_carts @ view_x_arr).mean())
    cy = float((non_h_carts @ view_y_arr).mean())

    # Step 1: initial radial placement (vectorised)
    delta = label_xy - np.array([cx, cy])
    norms = np.linalg.norm(delta, axis=1)
    safe_mask = norms < 0.05
    direction = np.where(
        safe_mask[:, None],
        np.array([0.0, 1.0]),
        np.divide(delta, np.where(norms[:, None] == 0, 1.0, norms[:, None])),
    )
    scale = (ellipse_rs + base_offset)[:, None]
    positions = label_xy + direction * scale

    # Step 2: iterative repulsion. Two coupled forces:
    #   (a) label<->label - labels mustn't write on top of each other.
    #   (b) label<->atom  - labels mustn't write on top of *another* atom's
    #                       ellipsoid. The label list is one-per-
    #                       crystallographic-label (deduplicated by the
    #                       caller), but the scene usually contains
    #                       additional drawn atoms - symmetry images and
    #                       disorder alternates with the same label - that
    #                       share a label position. Pass ``all_atoms`` to
    #                       this routine so those ghost ellipsoids
    #                       participate in the repulsion; otherwise labels
    #                       can land on top of them.
    label_r = 0.55  # text half-width in Å (3-4 chars at 7 pt)
    min_sep = label_r * 2.0

    atom_carts = np.array([a['cart'] for a in all_non_h_atoms], dtype=float)
    atom_xy = np.column_stack(
        (atom_carts @ view_x_arr, atom_carts @ view_y_arr)
    )  # (M, 2)
    atom_er = np.array(
        [_label_atom_radius(at, view_x_arr, view_y_arr) for at in all_non_h_atoms],
        dtype=float,
    )

    eps = 1e-6
    move_eps = 1e-3
    n_lab = len(positions)
    n_atom = len(atom_xy)

    # Skip the iterative force-directed pass when the structure has
    # too many atoms for the (N_lab, N_atom, 2) tensor to be cheap.
    # 1500+ atoms = 1.2M-cell tensor per iteration × 80 iterations =
    # several seconds even after vectorisation. The user looking at a
    # 2x2x2 unit-cell supercell of a 200-atom asymmetric unit doesn't
    # need pixel-perfect label placement -- the labels overlap each
    # other regardless. Drop straight to the radial Step-1 placement.
    LABEL_OPT_MAX_ATOMS = 600
    if n_atom > LABEL_OPT_MAX_ATOMS:
        result = [
            positions[k, 0] * view_x_arr + positions[k, 1] * view_y_arr
            for k in range(n_lab)
        ]
        _LABEL_POS_CACHE[cache_key] = result
        if len(_LABEL_POS_CACHE) > _LABEL_POS_CACHE_MAX:
            _LABEL_POS_CACHE.popitem(last=False)
        return [pos.copy() for pos in result]

    # ``owner_mask[i, k]`` = True if atom ``k`` is the same crystallographic
    # site as label ``i`` (so label i should not repel from atom k).
    owner_mask = (
        (np.abs(label_xy[:, 0:1] - atom_xy[:, 0]) < 1e-6)
        & (np.abs(label_xy[:, 1:2] - atom_xy[:, 1]) < 1e-6)
    )

    # Spatial pre-filter: for each label, only consider atoms within a
    # bounding window around it. The maximum radius an atom-ellipsoid
    # can repel a label from is ``max_er + label_r * 0.85 + 1`` (Å); we
    # use cKDTree for the lookup so the per-iteration force assembly
    # stays O(N_lab × few neighbours) instead of O(N_lab × N_atom).
    use_kdtree = n_atom > 64
    nearby_idx: list[np.ndarray] | None = None
    if use_kdtree:
        try:
            from scipy.spatial import cKDTree
            tree = cKDTree(atom_xy)
            cutoff = float(atom_er.max()) + label_r * 0.85 + 0.5
            # Re-query inside the loop (positions move) but only when
            # a label has likely moved out of its bucket. Cheap version:
            # run query once per iteration. Even at 80 iterations × 24
            # labels × log(N_atom) it's microseconds.
            def query_nearby(pos_xy: np.ndarray) -> list[np.ndarray]:
                neigh = tree.query_ball_point(pos_xy, r=cutoff)
                return [np.asarray(idx, dtype=int) for idx in neigh]

            nearby_idx = query_nearby(positions)
        except ImportError:
            use_kdtree = False

    for _ in range(80):
        # ----- label <-> label forces -----
        diff_ll = positions[:, None, :] - positions[None, :, :]  # (N, N, 2)
        dist_ll = np.sqrt((diff_ll ** 2).sum(axis=2))  # (N, N)
        np.fill_diagonal(dist_ll, np.inf)
        active_ll = (dist_ll < min_sep) & (dist_ll > eps)
        if active_ll.any():
            push_ll = np.where(active_ll, (min_sep - dist_ll) / 2.0 + 0.02, 0.0)
            inv_ll = np.where(active_ll, 1.0 / np.where(dist_ll == 0, 1.0, dist_ll), 0.0)
            unit_ll = diff_ll * inv_ll[:, :, None]
            force_ll = (unit_ll * push_ll[:, :, None]).sum(axis=1)
        else:
            force_ll = np.zeros_like(positions)

        # ----- label <-> atom forces -----
        if use_kdtree and nearby_idx is not None:
            force_la = np.zeros_like(positions)
            for i, idx_arr in enumerate(nearby_idx):
                if idx_arr.size == 0:
                    continue
                diff = positions[i, None, :] - atom_xy[idx_arr, :]  # (k, 2)
                dist = np.sqrt((diff ** 2).sum(axis=1))  # (k,)
                req = atom_er[idx_arr] + label_r * 0.85
                # Mask out the owner atom (same screen position).
                mask = (dist < req) & (dist > eps) & (~owner_mask[i, idx_arr])
                if not mask.any():
                    continue
                push = np.where(mask, (req - dist) + 0.02, 0.0)
                inv = np.where(mask, 1.0 / np.where(dist == 0, 1.0, dist), 0.0)
                unit = diff * inv[:, None]
                force_la[i] += (unit * push[:, None]).sum(axis=0)
        else:
            diff_la = positions[:, None, :] - atom_xy[None, :, :]  # (N, M, 2)
            dist_la = np.sqrt((diff_la ** 2).sum(axis=2))  # (N, M)
            req = atom_er[None, :] + label_r * 0.85
            active_la = (dist_la < req) & (dist_la > eps) & (~owner_mask)
            if active_la.any():
                push_la = np.where(active_la, (req - dist_la) + 0.02, 0.0)
                inv_la = np.where(active_la, 1.0 / np.where(dist_la == 0, 1.0, dist_la), 0.0)
                unit_la = diff_la * inv_la[:, :, None]
                force_la = (unit_la * push_la[:, :, None]).sum(axis=1)
            else:
                force_la = np.zeros_like(positions)

        delta_step = force_ll + force_la
        max_move = float(np.linalg.norm(delta_step, axis=1).max() if n_lab else 0.0)
        if max_move < move_eps:
            break
        positions = positions + delta_step
        # Re-query KDTree neighbours every few iterations as labels drift.
        if use_kdtree and nearby_idx is not None and max_move > 0.5:
            nearby_idx = query_nearby(positions)

    result = [
        positions[k, 0] * view_x_arr + positions[k, 1] * view_y_arr
        for k in range(n_lab)
    ]
    _LABEL_POS_CACHE[cache_key] = result
    if len(_LABEL_POS_CACHE) > _LABEL_POS_CACHE_MAX:
        _LABEL_POS_CACHE.popitem(last=False)
    return [pos.copy() for pos in result]


def _label_atom_radius(atom, view_x, view_y):
    """Return ``max(a_ax, b_ax)`` for ``atom``'s view-plane ellipse,
    skipping the polygon parametrisation that
    :func:`ellipsoid_3d_polygon` does for rendering. Used by the
    label-placement loop where only the bounding radius matters.
    """
    elem = atom.get('elem', 'C')
    U = atom.get('U')
    if U is not None and elem != 'H':
        try:
            P = np.array([view_x, view_y], dtype=float)
            U2 = P @ np.asarray(U, dtype=float) @ P.T
            U2 = (U2 + U2.T) / 2.0
            eigvals = np.linalg.eigvalsh(U2)
            eigvals = np.abs(eigvals)
            scale = np.sqrt(1.3863)  # 50 % probability
            a_ax = max(0.05, min(scale * np.sqrt(eigvals[0]), 0.40))
            b_ax = max(0.05, min(scale * np.sqrt(eigvals[1]), 0.40))
            return max(a_ax, b_ax)
        except np.linalg.LinAlgError:
            return 0.11
    if elem == 'H':
        return 0.07
    uiso = max(atom.get('uiso', 0.04) or 0.04, 0.02)
    r_atom = atom_r(elem)
    r = max(r_atom * 0.8, min(np.sqrt(1.3863 * uiso) * 0.65, r_atom * 1.3))
    return float(r)


def _label_offset_3d(at, all_atoms, view_x, view_y, base_offset=0.38):
    """
    Single-atom fallback (used when _compute_label_positions is not called).
    Places the label radially outward from the structure centroid.
    """
    _, a_ax, b_ax = ellipsoid_3d_polygon(at, view_x, view_y, n_pts=4)
    ellipse_r = max(a_ax, b_ax)

    non_h = [a for a in all_atoms if a['elem'] != 'H']
    if len(non_h) < 2:
        return view_y * (ellipse_r + base_offset)

    carts = np.array([a['cart'] for a in non_h])
    cx = float(np.mean(carts @ view_x))
    cy = float(np.mean(carts @ view_y))

    ax_s = float(at['cart'] @ view_x)
    ay_s = float(at['cart'] @ view_y)

    dx = ax_s - cx
    dy = ay_s - cy
    dist = math.sqrt(dx*dx + dy*dy)
    if dist < 0.05:
        dx, dy = 0.0, 1.0
    else:
        dx /= dist
        dy /= dist

    scale = ellipse_r + base_offset
    return (dx * scale) * view_x + (dy * scale) * view_y

# ── Draw crystal axes as 3D quiver objects near the structure ────────────────
def add_axes_overlay(ax, R, M, draw_atoms, view_x, view_y):
    """
    Draw a/b/c axis arrows as 3D quiver objects placed near the structure.
    All arrows are black; labels are italic a/b/c in black.
    The c-axis is always drawn even if nearly perpendicular to the screen.
    """
    a_cart = M[:, 0] / np.linalg.norm(M[:, 0])
    b_cart = M[:, 1] / np.linalg.norm(M[:, 1])
    c_cart = M[:, 2] / np.linalg.norm(M[:, 2])

    # All axes black, italic labels
    axis_info = [
        (a_cart, '$a$'),
        (b_cart, '$b$'),
        (c_cart, '$c$'),
    ]

    non_H = [at for at in draw_atoms if at['elem'] != 'H']
    if not non_H:
        return

    carts = np.array([at['cart'] for at in non_H])

    # Project to screen space (view_x = screen right, view_y = screen up)
    sx = carts @ view_x
    sy = carts @ view_y
    sz = carts @ R[2]

    sxmin, sxmax = sx.min(), sx.max()
    symin, symax = sy.min(), sy.max()
    sx_range = max(sxmax - sxmin, 1.0)
    sy_range = max(symax - symin, 1.0)

    # Arrow length in Å: ~18% of the larger screen-space extent
    arrow_len_ang = max(sx_range, sy_range) * 0.18
    label_ext = 1.6

    # Four candidate corners, offset just outside the bbox
    offset = arrow_len_ang * 1.2
    corners_ss = [
        (sxmin - offset, symin - offset),   # bottom-left
        (sxmax + offset, symin - offset),   # bottom-right
        (sxmin - offset, symax + offset),   # top-left
        (sxmax + offset, symax + offset),   # top-right
    ]

    # Score each corner: prefer the corner with fewest nearby atoms.
    # Use a larger radius to count atoms that would be obscured by the axis indicator.
    radius = max(sx_range, sy_range) * 0.35
    best_ss = corners_ss[0]
    best_score = np.inf
    for cx_ss, cy_ss in corners_ss:
        n_near = int(np.sum((sx - cx_ss)**2 + (sy - cy_ss)**2 < radius**2))
        # Score: only penalize atom overlap (no centroid distance term)
        score = float(n_near)
        if score < best_score:
            best_score = score
            best_ss = (cx_ss, cy_ss)

    sz_mean = float(sz.mean())
    ox_ss, oy_ss = best_ss
    view_z = R[2]
    origin_3d = ox_ss * view_x + oy_ss * view_y + sz_mean * view_z

    for cart_vec, label in axis_info:
        px = float(np.dot(cart_vec, view_x))
        py = float(np.dot(cart_vec, view_y))
        pnorm = np.sqrt(px*px + py*py)
        # Always draw: if nearly perpendicular to screen, use a minimum length
        # so the c-axis (often along view_z) still appears as a short stub
        if pnorm < 0.15:
            # Axis is nearly into/out of screen — draw a short dot-like stub
            # pointing slightly in the dominant screen direction
            pnorm = 0.15
        dx_ss = (px / pnorm) * arrow_len_ang
        dy_ss = (py / pnorm) * arrow_len_ang
        arrow_3d = dx_ss * view_x + dy_ss * view_y

        ox, oy, oz = origin_3d
        ax.quiver(ox, oy, oz,
                  arrow_3d[0], arrow_3d[1], arrow_3d[2],
                  color='black', lw=1.5, arrow_length_ratio=0.35,
                  linewidth=1.5, zorder=20)

        lx3d = origin_3d + arrow_3d * label_ext
        ax.text(lx3d[0], lx3d[1], lx3d[2], label,
                fontsize=8, color='black',
                ha='center', va='center', zorder=21,
                bbox=dict(boxstyle='round,pad=0.12', fc='white',
                          ec='none', alpha=0.90))

def _scene_ops():
    return SimpleNamespace(
        parse_asu=parse_asu,
        select_formula_unit=select_formula_unit,
        find_bonds=find_bonds,
        auto_view_dir=auto_view_dir,
        view_rotation=view_rotation,
        disorder_alpha=disorder_alpha,
        is_minor=is_minor,
        elem_color=elem_color,
        elem_color_light=elem_color_light,
        atom_r=atom_r,
        compute_label_positions=_compute_label_positions,
    )


def _apply_scene_axes(ax, scene):
    view_y = scene['view_y']
    view_z = scene['view_z']
    elev, azim = view_vec_to_elev_azim(view_z)
    elev_r = np.radians(elev)
    azim_r = np.radians(azim)
    up_default = np.array([-np.sin(elev_r)*np.cos(azim_r),
                           -np.sin(elev_r)*np.sin(azim_r),
                            np.cos(elev_r)])
    up_proj = up_default - np.dot(up_default, view_z) * view_z
    norm_up = np.linalg.norm(up_proj)
    if norm_up > 1e-6:
        up_proj /= norm_up
        cos_roll = np.clip(np.dot(up_proj, view_y), -1, 1)
        sin_roll = np.dot(np.cross(up_proj, view_y), view_z)
        roll = np.degrees(np.arctan2(sin_roll, cos_roll))
    else:
        roll = 0.0

    try:
        ax.view_init(elev=elev, azim=azim, roll=roll)
    except TypeError:
        ax.view_init(elev=elev, azim=azim)
    if scene.get('style', {}).get('projection') == 'orthographic':
        ax.set_proj_type('ortho')

    ax.set_axis_off()
    ax.xaxis.pane.fill = False
    ax.yaxis.pane.fill = False
    ax.zaxis.pane.fill = False
    ax.xaxis.pane.set_edgecolor('none')
    ax.yaxis.pane.set_edgecolor('none')
    ax.zaxis.pane.set_edgecolor('none')
    ax.grid(False)

    bounds = scene['bounds']
    mins = bounds['mins']
    maxs = bounds['maxs']
    sx_range, sy_range, sz_range = bounds['screen_ranges']
    half_x = sx_range / 2
    half_y = sy_range / 2
    half_z = sz_range / 2
    xmid = (mins[0] + maxs[0]) / 2
    ymid = (mins[1] + maxs[1]) / 2
    zmid = (mins[2] + maxs[2]) / 2
    max_half = max(half_x, half_y, half_z)
    ax.set_xlim(xmid - max_half, xmid + max_half)
    ax.set_ylim(ymid - max_half, ymid + max_half)
    ax.set_zlim(zmid - max_half, zmid + max_half)
    ax.set_box_aspect([sx_range, sy_range, sz_range])
    ax.set_title(scene['title'], fontsize=10, fontweight='bold', pad=5)
    if scene['has_minor']:
        ax.text2D(0.50, 0.02, 'Faded: minor disorder component',
                  transform=ax.transAxes, fontsize=5.5, color='#666666',
                  va='bottom', ha='center')


def draw_scene(ax, scene):
    view_x = scene['view_x']
    view_y = scene['view_y']
    depth_enabled = bool(scene.get('style', {}).get('depth_cue_enabled', False))

    for pass_minor in [True, False]:
        for bond in scene['bonds']:
            if pass_minor != bond['is_minor']:
                continue
            ai = scene['draw_atoms'][bond['i']]
            aj = scene['draw_atoms'][bond['j']]
            depth_t = bond['depth_t'] if depth_enabled else None
            draw_bond_3d(ax, ai, aj, bond['alpha_i'], bond['alpha_j'], depth_t=depth_t)

    for pass_minor in [True, False]:
        for at in scene['draw_atoms']:
            if pass_minor != at['is_minor']:
                continue
            depth_t = at['_depth_t'] if depth_enabled else None
            draw_atom_3d(ax, at, view_x, view_y, at['disorder_alpha'], depth_t=depth_t)

    _apply_scene_axes(ax, scene)
    label_data = [
        (
            item['atom_cart'].copy(),
            item['label_cart'].copy(),
            item['text'],
            item['is_minor'],
        )
        for item in scene['label_items']
    ]
    return scene['draw_atoms'], view_x, view_y, label_data


# ── Draw structure using Axes3D ──────────────────────────────────────────────
def draw_structure(ax, atoms, R, M, cell, title, show_H=False):
    scene = build_scene_from_atoms(
        _scene_ops(),
        name=title,
        title=title,
        atoms=atoms,
        cell=cell,
        M=M,
        R=R,
        show_hydrogen=show_H,
        preset=default_preset(),
    )
    return draw_scene(ax, scene)

# ── Draw labels in 3D space, called AFTER canvas.draw() ─────────────────────
def draw_labels_2d(ax, label_data, view_x, view_y):
    """
    Draw atom labels using ax.text in 3D space.
    Called AFTER fig.canvas.draw() so that labels are drawn on top of the
    already-rendered 3D geometry (Poly3DCollection objects).

    label_data: list of (atom_cart, lpos_cart, text, is_minor)
    """
    for atom_cart, lpos_cart, text, minor in label_data:
        lx, ly, lz = lpos_cart

        if minor:
            # Minor disorder: thin leader line + gray label
            ax.plot([atom_cart[0], lx],
                    [atom_cart[1], ly],
                    [atom_cart[2], lz],
                    '-', color='#888888', lw=0.5, zorder=200)
            ax.text(lx, ly, lz, text,
                    fontsize=4.5, ha='center', va='center',
                    color='#666666', zorder=201,
                    bbox=dict(boxstyle='round,pad=0.08', fc='white',
                              ec='none', alpha=1.0))
        else:
            ax.text(lx, ly, lz, text,
                    fontsize=5.5, fontweight='bold',
                    ha='center', va='center',
                    color='#111111', zorder=201,
                    bbox=dict(boxstyle='round,pad=0.10', fc='white',
                              ec='none', alpha=1.0))

# ── Auto in-plane rotation ──────────────────────────────────────────────────
def best_inplane_rotation(R, atoms, M, cell):
    atoms_copy = [dict(a) for a in atoms]
    try:
        _, sel_idxs = select_formula_unit(atoms_copy, M, cell)
        sel_atoms = [atoms_copy[i] for i in sel_idxs]
        major = [at for at in sel_atoms if is_major(at) and at['elem'] != 'H']
        if len(major) < 3:
            return R
        coords = np.array([at['cart'] for at in major])
    except:
        return R

    view_axis = R[2]
    best_R = R
    best_score = np.inf

    for deg in range(0, 360, 5):
        theta = np.radians(deg)
        c, s = np.cos(theta), np.sin(theta)
        K = np.array([[0, -view_axis[2], view_axis[1]],
                      [view_axis[2], 0, -view_axis[0]],
                      [-view_axis[1], view_axis[0], 0]])
        rot = c*np.eye(3) + s*K + (1-c)*np.outer(view_axis, view_axis)
        R_new = rot @ R

        sx = coords @ R_new[0]
        sy = coords @ R_new[1]
        w = sx.max() - sx.min()
        h = sy.max() - sy.min()
        if h < 1e-6 or w < 1e-6:
            continue
        aspect = max(w/h, h/w)
        if aspect < best_score:
            best_score = aspect
            best_R = R_new

    return best_R

def _split_formula_unit_atoms(atoms, sel_idxs):
    sel_atoms = [atoms[i] for i in sel_idxs]
    clusters = cluster_atoms(sel_atoms)
    org_local = []
    anion_local = []
    for idxs in clusters.values():
        elems = {sel_atoms[i]['elem'] for i in idxs if sel_atoms[i]['elem'] != 'H'}
        if 'Cl' in elems:
            anion_local.extend(idxs)
        elif 'C' in elems or 'N' in elems:
            org_local.extend(idxs)
    if not org_local:
        org_local = [i for i, at in enumerate(sel_atoms) if at['elem'] != 'H']
    return sel_atoms, org_local, anion_local

def _sphere_view_grid(n_elev=25, n_azim=48):
    vecs = []
    for ie in range(n_elev):
        elev = np.radians(-75.0 + ie * (150.0 / max(n_elev - 1, 1)))
        cos_e = np.cos(elev)
        sin_e = np.sin(elev)
        for ia in range(n_azim):
            azim = np.radians(ia * 360.0 / n_azim)
            vecs.append(np.array([cos_e * np.cos(azim),
                                  cos_e * np.sin(azim),
                                  sin_e]))
    return vecs

def _pick_up_vector(view_vec, candidates):
    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    best = None
    best_norm = -1.0
    for cand in candidates:
        c = np.array(cand, dtype=float)
        c_norm = np.linalg.norm(c)
        if c_norm < 1e-8:
            continue
        c /= c_norm
        screen_up = c - np.dot(c, v) * v
        screen_norm = np.linalg.norm(screen_up)
        if screen_norm > best_norm:
            best = screen_up / screen_norm if screen_norm > 1e-8 else None
            best_norm = screen_norm
    if best is not None:
        return best
    fallback = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(fallback, v)) > 0.95:
        fallback = np.array([0.0, 1.0, 0.0])
    return fallback

VIEW_SCORE_WEIGHTS = {
    'default': {
        'organic_plane': 1.05,
        'organic_depth': 0.85,
        'aspect': 0.20,
        'robust_sep': 0.40,
        'close_contact': 1.15,
        'occlusion': 1.70,
        'cluster_crowding': 1.35,
        'elev_pen': 1.25,
    },
    'MPEP': {
        'organic_plane': 0.90,
        'organic_depth': 1.10,
        'close_contact': 1.35,
        'occlusion': 2.10,
        'cluster_crowding': 1.55,
    },
    'HPEP': {
        'organic_plane': 0.90,
        'organic_depth': 1.15,
        'close_contact': 1.25,
        'occlusion': 1.95,
        'cluster_crowding': 1.90,
    },
}

def _resolve_view_score_weights(name):
    weights = dict(VIEW_SCORE_WEIGHTS['default'])
    if name in VIEW_SCORE_WEIGHTS:
        weights.update(VIEW_SCORE_WEIGHTS[name])
    return weights

def _classify_clusters(atoms):
    clusters = cluster_atoms(atoms)
    organic = []
    anion = []
    for idxs in clusters.values():
        elems = {atoms[i]['elem'] for i in idxs if atoms[i]['elem'] != 'H'}
        if 'Cl' in elems:
            anion.append(sorted(idxs))
        elif 'C' in elems or 'N' in elems:
            organic.append(sorted(idxs))
    return organic, anion

def _build_pair_exclusions(n_atoms, bond_pairs):
    adjacency = [set() for _ in range(n_atoms)]
    excluded = set()
    for i, j in bond_pairs:
        if i > j:
            i, j = j, i
        excluded.add((i, j))
        adjacency[i].add(j)
        adjacency[j].add(i)
    for i in range(n_atoms):
        for mid in adjacency[i]:
            for j in adjacency[mid]:
                if j == i:
                    continue
                a, b = sorted((i, j))
                excluded.add((a, b))
    return excluded

def _pair_weight(i, j, org_set, anion_set):
    i_org = i in org_set
    j_org = j in org_set
    i_ani = i in anion_set
    j_ani = j in anion_set
    if i_org and j_org:
        return 1.25
    if (i_org and j_ani) or (j_org and i_ani):
        return 1.40
    if i_ani and j_ani:
        return 0.90
    return 1.00

def _cluster_shape_p80(pts, cluster_radii):
    """Return ``(centroid, radial_p80)`` for a cluster's screen-space
    extent. Replaces ``np.percentile(radial, 80)`` -- per-view we call
    this 6+ times across 1000+ candidate views, and ``np.percentile``
    has ~50 us of dispatch overhead per call for trivial-sized inputs
    that dominate the function. Sorting + interpolation matches numpy's
    default linear interpolation mode and runs in <2 us for the typical
    5-10-atom cluster.
    """
    centroid = pts.mean(axis=0)
    radial = np.sqrt(((pts - centroid) ** 2).sum(axis=1)) + cluster_radii
    n = radial.size
    if n == 0:
        return centroid, 0.0
    if n == 1:
        return centroid, float(radial[0])
    sorted_r = np.sort(radial)
    rank = 0.8 * (n - 1)
    lo = int(np.floor(rank))
    hi = int(np.ceil(rank))
    frac = rank - lo
    if lo == hi:
        return centroid, float(sorted_r[lo])
    return centroid, float(sorted_r[lo] * (1 - frac) + sorted_r[hi] * frac)


def _cluster_crowding_penalty(pts_2d, radii, org_clusters, anion_clusters):
    def cluster_shape(idxs):
        if not idxs:
            return None
        idx_arr = np.asarray(idxs, dtype=int)
        return _cluster_shape_p80(pts_2d[idx_arr], radii[idx_arr])

    penalty = 0.0
    org_shapes = [cluster_shape(idxs) for idxs in org_clusters if idxs]
    ani_shapes = [cluster_shape(idxs) for idxs in anion_clusters if idxs]
    org_shapes = [item for item in org_shapes if item is not None]
    ani_shapes = [item for item in ani_shapes if item is not None]

    for oc, orad in org_shapes:
        for ac, arad in ani_shapes:
            dist = np.linalg.norm(oc - ac)
            thresh = 0.90 * (orad + arad)
            if dist < thresh:
                penalty += ((thresh - dist) / max(thresh, 1e-6)) ** 2
    for i in range(len(ani_shapes)):
        for j in range(i + 1, len(ani_shapes)):
            ci, ri = ani_shapes[i]
            cj, rj = ani_shapes[j]
            dist = np.linalg.norm(ci - cj)
            thresh = 0.72 * (ri + rj)
            if dist < thresh:
                penalty += 0.55 * ((thresh - dist) / max(thresh, 1e-6)) ** 2
    return penalty

def _view_plane_basis(view_vec):
    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    anchor = np.array([0.0, 0.0, 1.0]) if abs(v[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    ex = np.cross(anchor, v)
    ex /= np.linalg.norm(ex)
    ey = np.cross(v, ex)
    ey /= np.linalg.norm(ey)
    return ex, ey

def _perturb_view(view_vec, dx_deg, dy_deg):
    ex, ey = _view_plane_basis(view_vec)
    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    candidate = v + np.tan(np.radians(dx_deg)) * ex + np.tan(np.radians(dy_deg)) * ey
    candidate /= np.linalg.norm(candidate)
    return candidate

def _build_pair_weight_matrix(n, org_pos, anion_pos):
    """Precompute the per-pair occlusion-penalty weights once per
    ``auto_view_dir`` call. Replaces the in-loop ``_pair_weight``
    call which used to dominate ``_score_auto_view`` because it ran
    O(N^2) Python lookups per view × 1000+ candidate views.
    """
    is_org = np.zeros(n, dtype=bool)
    is_ani = np.zeros(n, dtype=bool)
    if org_pos:
        is_org[np.asarray(list(org_pos), dtype=int)] = True
    if anion_pos:
        is_ani[np.asarray(list(anion_pos), dtype=int)] = True
    i_org = is_org[:, None]
    j_org = is_org[None, :]
    i_ani = is_ani[:, None]
    j_ani = is_ani[None, :]
    both_org = i_org & j_org
    org_ani_cross = (i_org & j_ani) | (j_org & i_ani)
    both_ani = i_ani & j_ani
    return np.where(
        both_org, 1.25,
        np.where(
            org_ani_cross, 1.40,
            np.where(both_ani, 0.90, 1.00),
        ),
    )


def _build_excluded_mask(n, excluded_pairs):
    """Precompute the symmetric (N, N) bool mask of bond-excluded
    pairs so the per-view occlusion sum can run as a single
    vectorised reduction."""
    mask = np.zeros((n, n), dtype=bool)
    if excluded_pairs:
        idx = np.array(list(excluded_pairs), dtype=int)
        mask[idx[:, 0], idx[:, 1]] = True
        mask[idx[:, 1], idx[:, 0]] = True
    return mask


def _score_auto_view(coords, radii, org_pos, anion_pos, org_clusters, anion_clusters,
                     excluded_pairs, weights, view_vec,
                     pair_weight_matrix=None, excluded_mask=None):
    R = view_rotation(view_vec)
    sx = coords @ R[0]
    sy = coords @ R[1]
    sz = coords @ R[2]
    pts_2d = np.stack([sx, sy], axis=1)

    org_idx = np.array(org_pos, dtype=int)
    org_2d = pts_2d[org_idx]
    org_center = org_2d.mean(axis=0)
    org_cov = np.cov((org_2d - org_center).T) if len(org_2d) > 2 else np.eye(2) * 1e-4
    eigvals = np.clip(np.linalg.eigvalsh(org_cov), 1e-8, None)
    organic_plane = float(np.sqrt(eigvals[0] * eigvals[1]))
    # Combined p10/p90 via a single sort instead of two ``np.percentile``
    # calls (50 us each in dispatch overhead) -- this runs once per view
    # candidate × 1000+ candidates per ``auto_view_dir`` call.
    sz_org = np.sort(sz[org_idx])
    n_org = sz_org.size
    if n_org < 2:
        org_depth = 0.0
    else:
        rank_lo = 0.10 * (n_org - 1)
        rank_hi = 0.90 * (n_org - 1)
        lo_lo = int(np.floor(rank_lo))
        lo_hi = int(np.ceil(rank_lo))
        hi_lo = int(np.floor(rank_hi))
        hi_hi = int(np.ceil(rank_hi))
        p10 = sz_org[lo_lo] + (rank_lo - lo_lo) * (sz_org[lo_hi] - sz_org[lo_lo])
        p90 = sz_org[hi_lo] + (rank_hi - hi_lo) * (sz_org[hi_hi] - sz_org[hi_lo])
        org_depth = float(p90 - p10)

    all_w = sx.max() - sx.min()
    all_h = sy.max() - sy.min()
    asp = min(all_w, all_h) / max(all_w, all_h) if max(all_w, all_h) > 1e-6 else 0.0

    diffs = pts_2d[:, None, :] - pts_2d[None, :, :]
    dists = np.sqrt((diffs**2).sum(axis=2) + 1e-12)
    dz = np.abs(sz[:, None] - sz[None, :])
    thresh = 0.78 * (radii[:, None] + radii[None, :])

    n = len(coords)
    if pair_weight_matrix is None:
        pair_weight_matrix = _build_pair_weight_matrix(n, org_pos, anion_pos)
    if excluded_mask is None:
        excluded_mask = _build_excluded_mask(n, excluded_pairs)

    # Vectorised occlusion sum: replaces the O(N^2) Python loop that
    # used to call ``_pair_weight`` per pair × 1000+ views, which was
    # the inner hot-loop of ``auto_view_dir``. Mask is upper-triangular
    # so each unordered pair contributes once.
    upper = np.triu(np.ones((n, n), dtype=bool), k=1)
    overlap_mat = thresh - dists
    safe_thresh = np.maximum(thresh, 1e-6)
    active = upper & (~excluded_mask) & (overlap_mat > 0)
    if active.any():
        depth_scale = np.clip(1.0 - dz / safe_thresh, 0.0, 1.0)
        contrib = pair_weight_matrix * ((overlap_mat / safe_thresh) ** 2) * (1.0 + 1.6 * depth_scale)
        occlusion = float(contrib[active].sum())
    else:
        occlusion = 0.0

    robust_sep = 0.0
    close_contact = 0.0
    if anion_pos:
        anion_idx = np.array(anion_pos, dtype=int)
        org_ani_diffs = org_2d[:, None, :] - pts_2d[anion_idx][None, :, :]
        org_ani_dists = np.sqrt((org_ani_diffs**2).sum(axis=2) + 1e-12)
        org_thresh = 0.88 * (radii[org_idx][:, None] + radii[anion_idx][None, :])
        flat_dists = np.sort(org_ani_dists, axis=None)
        robust_sep = float(np.mean(flat_dists[:min(6, len(flat_dists))]))
        overlap_oa = np.clip(org_thresh - org_ani_dists, 0.0, None)
        depth_scale = np.clip(1.0 - np.abs(sz[org_idx][:, None] - sz[anion_idx][None, :]) /
                              np.maximum(org_thresh, 1e-6), 0.0, 1.0)
        close_contact = float(np.sum((overlap_oa / np.maximum(org_thresh, 1e-6)) *
                                     (1.0 + 1.2 * depth_scale)))

    cluster_crowding = _cluster_crowding_penalty(pts_2d, radii, org_clusters, anion_clusters)

    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    elev_deg = np.degrees(np.arcsin(np.clip(v[2], -1, 1)))
    elev_pen = max(0.0, (abs(elev_deg) - 55.0) / 25.0)

    score = (
        organic_plane * weights['organic_plane'] +
        org_depth * weights['organic_depth'] +
        robust_sep * weights['robust_sep'] +
        asp * weights['aspect'] -
        close_contact * weights['close_contact'] -
        occlusion * weights['occlusion'] -
        cluster_crowding * weights['cluster_crowding'] -
        elev_pen * weights['elev_pen']
    )
    return score

_AUTO_VIEW_CACHE: "OrderedDict[tuple, tuple[np.ndarray, np.ndarray]]" = OrderedDict()
_AUTO_VIEW_CACHE_MAX = 64


def _auto_view_cache_key(atoms, M, cell, compound_name) -> tuple:
    """Stable hash of the inputs that drive ``auto_view_dir``.

    The function is deterministic in
    ``(atom positions, atom labels, M, cell, compound_name)``; nothing
    else affects the chosen view direction. Hashing the rounded
    Cartesian positions (4 decimals = 0.1 mAa) gives a key that
    survives copy / round-trip without false misses.
    """
    M_arr = np.asarray(M, dtype=float)
    pos_bytes = np.round(
        np.array([atom["cart"] for atom in atoms], dtype=float), 4
    ).tobytes()
    labels = tuple(str(atom.get("label") or atom.get("elem")) for atom in atoms)
    elems = tuple(str(atom.get("elem")) for atom in atoms)
    cell_key = None
    if cell is not None:
        try:
            cell_key = (
                round(float(cell.a), 5), round(float(cell.b), 5), round(float(cell.c), 5),
                round(float(cell.alpha), 4), round(float(cell.beta), 4), round(float(cell.gamma), 4),
            )
        except AttributeError:
            cell_key = tuple(np.round(np.asarray(cell, dtype=float), 5).flatten().tolist())
    return (
        len(atoms),
        pos_bytes,
        labels,
        elems,
        np.round(M_arr, 5).tobytes(),
        cell_key,
        str(compound_name or ""),
    )


def auto_view_dir(atoms, M, cell, compound_name=None):
    # The auto-view-direction picker is the dominant cost of
    # ``build_loaded_crystal`` (~9 s on DAP-4: ~3 s in the second-pass
    # ``select_formula_unit`` call below, ~6 s scoring 1080+ candidate
    # camera angles by O(N^2) projected-occlusion penalties). The
    # result is fully determined by the atoms / cell / compound name,
    # so memoising on a content hash is safe and turns every repeat
    # load (tests, REST round-trip, dev iteration) into a no-op.
    cache_key = _auto_view_cache_key(atoms, M, cell, compound_name)
    cached = _AUTO_VIEW_CACHE.get(cache_key)
    if cached is not None:
        _AUTO_VIEW_CACHE.move_to_end(cache_key)
        view_dir, up = cached
        return view_dir.copy(), up.copy()

    atoms_copy = [dict(a) for a in atoms]
    try:
        atoms_sel, sel_idxs = select_formula_unit(atoms_copy, M, cell)
        sel_atoms = [atoms_sel[i] for i in sel_idxs]
    except Exception:
        view_dir = np.array([0.174, 0.985, 0.000])
        up = np.array([0.0, 0.0, 1.0])
        _AUTO_VIEW_CACHE[cache_key] = (view_dir, up)
        if len(_AUTO_VIEW_CACHE) > _AUTO_VIEW_CACHE_MAX:
            _AUTO_VIEW_CACHE.popitem(last=False)
        return view_dir.copy(), up.copy()

    valid_atoms = [at for at in sel_atoms if at['elem'] != 'H' and is_major(at)]
    if len(valid_atoms) < 3:
        view_dir = np.array([0.174, 0.985, 0.000])
        up = np.array([0.0, 0.0, 1.0])
        _AUTO_VIEW_CACHE[cache_key] = (view_dir, up)
        if len(_AUTO_VIEW_CACHE) > _AUTO_VIEW_CACHE_MAX:
            _AUTO_VIEW_CACHE.popitem(last=False)
        return view_dir.copy(), up.copy()

    org_clusters, anion_clusters = _classify_clusters(valid_atoms)
    if not org_clusters:
        org_clusters = [list(range(len(valid_atoms)))]
    org_pos = sorted({idx for group in org_clusters for idx in group})
    anion_pos = sorted({idx for group in anion_clusters for idx in group})

    coords = np.array([at['cart'] for at in valid_atoms], dtype=float)
    radii = np.array([cov_r(at['elem']) for at in valid_atoms], dtype=float)
    org_coords = coords[np.array(org_pos)]
    centered = org_coords - org_coords.mean(axis=0)
    weights = _resolve_view_score_weights(compound_name)
    excluded_pairs = _build_pair_exclusions(len(valid_atoms), find_bonds(valid_atoms))

    try:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        pca_axes = [vt[0], vt[1], vt[2]]
    except np.linalg.LinAlgError:
        pca_axes = [np.array([1.0, 0.0, 0.0]),
                    np.array([0.0, 1.0, 0.0]),
                    np.array([0.0, 0.0, 1.0])]

    candidates = []
    seen = set()

    def add_candidate(vec):
        v = np.array(vec, dtype=float)
        n = np.linalg.norm(v)
        if n < 1e-8:
            return
        v /= n
        key = tuple(np.round(v, 4))
        if key not in seen:
            seen.add(key)
            candidates.append(v)

    for axis in pca_axes:
        add_candidate(axis)
        add_candidate(-axis)
    for vec in _sphere_view_grid(n_elev=19, n_azim=36):
        add_candidate(vec)

    n_atoms_view = len(coords)
    pair_weight_matrix = _build_pair_weight_matrix(n_atoms_view, org_pos, anion_pos)
    excluded_mask = _build_excluded_mask(n_atoms_view, excluded_pairs)

    ranked = []
    for view_vec in candidates:
        score = _score_auto_view(
            coords, radii, org_pos, anion_pos, org_clusters,
            anion_clusters, excluded_pairs, weights, view_vec,
            pair_weight_matrix=pair_weight_matrix,
            excluded_mask=excluded_mask,
        )
        ranked.append((score, view_vec))
    ranked.sort(key=lambda item: item[0], reverse=True)

    fine_candidates = []
    fine_seen = set()
    for _, base_vec in ranked[:8]:
        for dx_deg in (-14, -8, -4, 0, 4, 8, 14):
            for dy_deg in (-14, -8, -4, 0, 4, 8, 14):
                cand = _perturb_view(base_vec, dx_deg, dy_deg)
                key = tuple(np.round(cand, 5))
                if key in fine_seen:
                    continue
                fine_seen.add(key)
                fine_candidates.append(cand)

    best_score = ranked[0][0]
    best_view = ranked[0][1]
    for view_vec in fine_candidates:
        score = _score_auto_view(
            coords, radii, org_pos, anion_pos, org_clusters,
            anion_clusters, excluded_pairs, weights, view_vec,
            pair_weight_matrix=pair_weight_matrix,
            excluded_mask=excluded_mask,
        )
        if score > best_score:
            best_score = score
            best_view = view_vec

    up_vec = _pick_up_vector(best_view, pca_axes + [
        np.array([0.0, 0.0, 1.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
    ])
    _AUTO_VIEW_CACHE[cache_key] = (best_view, up_vec)
    if len(_AUTO_VIEW_CACHE) > _AUTO_VIEW_CACHE_MAX:
        _AUTO_VIEW_CACHE.popitem(last=False)
    return best_view.copy(), up_vec.copy()

# ── Main render function ─────────────────────────────────────────────────────
def _render(show_labels=True, preset_path=None, names=None):
    ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    preset = load_preset(preset_path) if preset_path else default_preset()
    scenes = build_default_scenes(_scene_ops(), root_dir=ws, preset=preset, names=names)
    fig = plt.figure(figsize=(18, 5))
    gs = fig.add_gridspec(2, 4, height_ratios=[8, 0.55],
                          hspace=0.05, wspace=0.02)
    # Use projection='3d' for all structure subplots
    axes = [fig.add_subplot(gs[0, 0], projection='3d'),
            fig.add_subplot(gs[0, 1], projection='3d'),
            fig.add_subplot(gs[0, 2], projection='3d'),
            fig.add_subplot(gs[0, 3], projection='3d')]
    ax_legend = fig.add_subplot(gs[1, :])
    ax_legend.axis('off')

    overlay_data = []   # (ax, R, M, draw_atoms, view_x, view_y, label_data)
    for idx, (name, scene) in enumerate(scenes.items()):
        print(f"Processing {name}...")
        print(f"  {name}: {len(scene['selected_atoms'])} selected atoms, {len(scene['draw_atoms'])} drawn")
        ax = axes[idx]
        vd = scene['view_direction']
        print(f"  {name} view = [{vd[0]:.3f}, {vd[1]:.3f}, {vd[2]:.3f}]")
        draw_atoms, view_x, view_y, label_data = draw_scene(ax, scene)
        overlay_data.append((ax, scene['R'], scene['M'], draw_atoms, view_x, view_y, label_data))

    # ── Legend ────────────────────────────────────────────────────────────────
    handles = [
        mpatches.Patch(color=elem_color('C'),  label='C'),
        mpatches.Patch(color=elem_color('N'),  label='N'),
        mpatches.Patch(color=elem_color('O'),  label='O'),
        mpatches.Patch(color=elem_color('Cl'), label='Cl'),
        Line2D([0],[0], color='#5A5A5A', lw=BOND_LW*0.5,
               label='Covalent bond (two-color)'),
        mpatches.Patch(facecolor='#888888', alpha=0.30,
                       label='Minor disorder (faded)'),
    ]
    ax_legend.legend(handles=handles, loc='center', fontsize=9,
                     framealpha=0.9, ncol=6, title='Legend', title_fontsize=9,
                     borderpad=0.8)

    fig.suptitle(
        'Crystal Structures (ORTEP-style, 50% probability ellipsoids)\n'
        'H atoms omitted  ·  One formula unit [A][B](ClO₄)₄ shown  ·  '
        'Disorder shown by opacity  ·  No bonds between conflicting disorder parts',
        fontsize=10, y=0.995)

    # ── Two-pass overlays: render first, then add axes + labels ──────────────
    # fig.canvas.draw() finalises the Axes3D projection matrices so that
    # ax.get_proj() returns correct values for 3D→2D projection.
    fig.canvas.draw()
    for ax, R, M, draw_atoms, view_x, view_y, label_data in overlay_data:
        add_axes_overlay(ax, R, M, draw_atoms, view_x, view_y)
        if show_labels:
            draw_labels_2d(ax, label_data, view_x, view_y)

    suffix = '' if show_labels else '_nolabel'
    out_dir = os.path.join(ws, '.exports')
    os.makedirs(out_dir, exist_ok=True)
    for ext in ('png', 'svg', 'pdf'):
        out = os.path.join(out_dir, f'crystal_structures{suffix}.{ext}')
        kw = dict(bbox_inches='tight', facecolor='white')
        if ext == 'png':
            kw['dpi'] = 300
        fig.savefig(out, **kw)
        print(f"Saved: {out}")
    plt.close()

def _build_parser():
    parser = argparse.ArgumentParser(description='Render crystal structure figure panels.')
    parser.add_argument('--preset', help='Path to a saved crystal view preset JSON.')
    parser.add_argument('--structure', action='append',
                        help='Render only selected structure(s). Can be repeated.')
    parser.add_argument('--labels', dest='show_labels', action='store_true',
                        help='Render the labeled panel set.')
    parser.add_argument('--no-labels', dest='show_labels', action='store_false',
                        help='Render the no-label panel set.')
    parser.add_argument('--both', action='store_true',
                        help='Render both labeled and unlabeled outputs.')
    parser.add_argument('--write-default-preset',
                        help='Write a starter preset JSON and exit.')
    parser.set_defaults(show_labels=None)
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.write_default_preset:
        save_preset(args.write_default_preset, default_preset())
        print(f"Saved default preset: {args.write_default_preset}")
        return

    names = args.structure or None
    if args.both or args.show_labels is None:
        _render(show_labels=True, preset_path=args.preset, names=names)
        _render(show_labels=False, preset_path=args.preset, names=names)
    else:
        _render(show_labels=bool(args.show_labels), preset_path=args.preset, names=names)

if __name__ == '__main__':
    main()
