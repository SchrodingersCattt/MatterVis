from __future__ import annotations

import plotly.graph_objects as go

from mat_viewer.render.html_export import write_interactive_html


def test_standalone_html_embeds_live_lattice_compass(tmp_path):
    fig = go.Figure(
        data=[
            go.Scatter3d(
                x=[0.0, 1.0],
                y=[0.0, 1.0],
                z=[0.0, 1.0],
            )
        ]
    )
    fig.update_layout(
        scene={
            "camera": {
                "eye": {"x": 1.0, "y": 1.0, "z": 1.0},
                "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            }
        },
        meta={
            "compass": {
                "M": [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]],
                "labels": ["a", "b", "c"],
                "colors": ["#CC0000", "#00AA00", "#0055CC"],
                "anchor": [0.10, 0.18],
            }
        },
    )
    output = tmp_path / "interactive.html"

    write_interactive_html(fig, output)

    html = output.read_text(encoding="utf-8")
    assert "{plot_id}" not in html
    assert "data-mattervis-compass" in html
    assert "__mvStandaloneCompass" in html
    assert "internal.getCamera()" in html
    assert 'gd.on("plotly_relayout"' in html
    assert 'svg.style.pointerEvents = "none"' in html
