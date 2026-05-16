from __future__ import annotations

import re

import gemmi
import numpy as np

from .geometry import _wrap_frac01, bond_vector_mic, ortho_matrix

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


__all__ = [name for name in globals() if not name.startswith("__")]
