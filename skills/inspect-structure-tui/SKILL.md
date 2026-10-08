---
name: inspect-structure-tui
description: "Use when a non-visual agent must inspect an atomistic structure through the MatterVis terminal session. Teaches the JSONL action loop only. Does not judge stereochemistry or supply answers."
---

# Inspect a structure in the terminal

Use this only for a perception-level MatterVis session. Do not select or focus
atoms. Selection opens a chemistry inspector that prints names, CIP labels, and
measurements; those are a different capability.

## Start

```bash
mat-vis tui INPUT --session-format jsonl --charset ascii7
```

Send one JSON object per line on stdin. Each object is
`mattervis.tui.action/v1`. Read one response before sending the next action.

```json
{"schema":"mattervis.tui.action/v1","action":"observe","arguments":{}}
{"schema":"mattervis.tui.action/v1","action":"orbit","arguments":{"yaw_deg":30,"pitch_deg":15}}
{"schema":"mattervis.tui.action/v1","action":"align","arguments":{"axis":"c"}}
{"schema":"mattervis.tui.action/v1","action":"close","arguments":{}}
```

## Allowed actions

`observe`, `reset`, `orbit`, `align`, `pan`, `zoom`, `fit`, `set_display`, `close`.

- Call `observe` before deciding the next action.
- A structured error does not end the session.
- After two invalid actions in a row, stop and answer from what you have already seen.
- `align` axes are `a`, `b`, `c`, `a*`, `b*`, `c*`.

This skill does not define the answer format and does not explain how to assign R or S.
