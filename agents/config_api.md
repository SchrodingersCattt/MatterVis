# MatterVis Config API

MatterVis exposes a read-only built-in configuration plus a user TOML
override file. The built-in config owns MatterVis rendering defaults
(style, colours, radii used by the renderer, selection highlight
colour, and cube rendering palettes). MolCrysKit chemistry defaults
remain MolCrysKit-owned; MatterVis only exposes optional
`mck_overrides` fields and forwards them when explicitly set.

## Python

```python
from crystal_viewer.config import CONFIG, reload_config

style = CONFIG.style.as_dict()
carbon = CONFIG.colors.get("elements", {})["C"]

reload_config()  # re-read ~/.config/mattervis/config.toml
```

`CONFIG` is read-only. To change defaults, edit the TOML override file
or use the REST API below.

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
  `crystal_viewer.presets.DEFAULT_STYLE`.
- `colors`: MatterVis scene palette, radii, polyhedron auto-colours,
  and `selection_highlight`.
- `cube`: cube/orbital panel palette and radii.
- `mck_overrides`: optional MolCrysKit kwargs. `None` / absent means
  "use MolCrysKit default".

Unknown keys are ignored so older MatterVis builds can safely read
newer config files.
