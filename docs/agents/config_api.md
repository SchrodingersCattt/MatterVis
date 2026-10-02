# MatterVis Config API

MatterVis exposes a read-only built-in configuration plus a user TOML
override file. The built-in config owns MatterVis rendering defaults
(style, colours, radii used by the renderer, selection highlight
colour, and cube rendering palettes). MolCrysKit chemistry defaults
remain MolCrysKit-owned; MatterVis only exposes optional
`mck_overrides` fields and forwards them when explicitly set.

## Python

```python
from mat_viewer.config import CONFIG, element_color, reload_config

style = CONFIG.style.as_dict()
carbon = CONFIG.colors.get("elements", {})["C"]
carbon_hex = element_color("C")
legacy_cpk_carbon = element_color("C", theme="cpk")

reload_config()  # re-read ~/.config/mattervis/config.toml
```

`CONFIG` is read-only. To change defaults, edit the TOML override file
or use the REST API below.

`element_color(symbol)` resolves the canonical MatterVis palette used by the
scene, publication, ORTEP, and terminal renderers. Charged labels such as
`Fe2+` are normalized before lookup. Pass `theme="cpk"` only when a cube or
orbital caller explicitly needs the historical CPK palette; the CPK theme has
no light variant and raises `ValueError` when `light=True`.

## REST

All endpoints live under `/api/v2`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/config` | Return the effective config and source paths. |
| `GET` | `/config/colors/elements` | Return element and light element palettes. |
| `PATCH` | `/config` | Write a user override TOML and reload config. Body is a JSON object. |
| `DELETE` | `/config` | Delete the user override TOML and reload built-ins. |
| `POST` | `/config/reload` | Re-read the configured TOML path. |

Example:

```bash
curl -X PATCH http://localhost:50001/api/v2/config \
  -H 'Content-Type: application/json' \
  -d '{"style":{"atom_scale":1.15},"colors":{"selection_highlight":"#FFD24A"}}'
```

## Schema

Top-level sections:

- `style`: default render style keys, mirroring
  `mat_viewer.presets.DEFAULT_STYLE`.
- `colors`: MatterVis scene palette, radii, polyhedron auto-colours,
  and `selection_highlight`.
- `cube`: cube/orbital panel palette and radii.
- `mck_overrides`: optional MolCrysKit kwargs. `None` / absent means
  "use MolCrysKit default".

`mck_overrides.bond_scale` is the global MolCrysKit bonding coefficient used
when a caller does not pass `bond_scale=`. The default is `1.0`; it scales
calibrated radius-based cutoffs and explicit pair thresholds.

`mck_overrides.bond_thresholds` is a TOML-safe list of records, for example:

```toml
[[mck_overrides.bond_thresholds]]
elements = ["Zn", "N"]
cutoff = 2.5
```

Python callers may use tuple keys such as `{("Zn", "N"): 2.5}`. Explicit
pair thresholds are scaled by the selected `bond_scale` and are forwarded to
all canonical loaders, not only cube loading.

`mat_viewer.config.element_color(symbol)` is the canonical semantic resolver.
All renderers use its default `theme="canonical"` palette. The historical
bright cube/CPK values remain available only through the explicit
`element_color(symbol, theme="cpk")` opt-in; a backend must not select that
theme implicitly. The terminal UI converts the canonical RGB result to an
ANSI-256 code only at its output boundary.

Unknown keys are ignored so older MatterVis builds can safely read
newer config files.
