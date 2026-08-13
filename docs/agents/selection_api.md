# MatterVis Selection API

Selection is a per-scene working set of atom labels. It is temporary UI
state, separate from persistent `atom_groups`. A selected atom label
highlights every displayed copy of that label (PBC / symmetry replicas
collapse to the same source identity).

## State Shape

```json
{
  "atom_labels": ["Cl1", "O3"],
  "active_label": "O3",
  "order": ["Cl1", "O3"]
}
```

## REST

All endpoints live under `/api/v2` and accept optional `scene_id`.

| Method | Path | Body | Description |
|---|---|---|---|
| `GET` | `/selection` | | Current selection. |
| `POST` | `/selection` | `{"atom_labels":["Cl1"],"replace":true}` | Replace or add labels. |
| `PATCH` | `/selection` | `{"add":["O1"],"remove":["Cl1"]}` | Incremental edit. |
| `DELETE` | `/selection` | | Clear selection. |
| `POST` | `/selection/by_fragment` | `{"fragment_label":"A0"}` | Select all atom labels in one MCK fragment. |
| `POST` | `/selection/by_element` | `{"element":"Cl"}` | Select all visible labels of an element. |
| `POST` | `/selection/all` | | Select all visible atom labels. |
| `POST` | `/selection/invert` | | Invert against visible atom labels. |
| `POST` | `/selection/promote` | `{"name":"picked atoms","color":"#FFD24A"}` | Create an `atom_group` from the selection and clear it. |

## Notes

- Selection identity is label-based by design; use `atom_groups` for
  persistent styling.
- `select_fragment` uses the fragment labels already generated from
  MolCrysKit molecule grouping. It does not re-run bond perception.
