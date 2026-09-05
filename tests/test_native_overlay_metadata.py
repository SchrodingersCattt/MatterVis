from __future__ import annotations

import numpy as np

from mat_viewer.render.mesh_overlays import isosurface_primitives
from mat_viewer.render.style import _atom_render_visible


def test_isosurface_record_preserves_native_renderer_metadata() -> None:
    scene = {
        "isosurfaces": [
            {
                "id": "cutoff",
                "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                "triangles": [[0, 1, 2]],
                "normals": [[0, 0, 1]] * 3,
                "color": "#4E9BB5",
                "opacity": 0.3,
                "metadata": {
                    "_raster_shape": "sphere",
                    "_raster_center": (0.5, 0.5, 0.0),
                    "_raster_radius": 0.5,
                },
            }
        ]
    }

    primitives, warnings = isosurface_primitives(scene)

    assert not warnings
    assert len(primitives) == 1
    primitive = primitives[0]
    assert primitive.metadata["_raster_shape"] == "sphere"
    assert primitive.metadata["_raster_center"] == (0.5, 0.5, 0.0)
    assert primitive.metadata["_raster_radius"] == 0.5


def test_zero_atom_opacity_is_hidden_instead_of_rendered_black() -> None:
    assert _atom_render_visible({"_render_visible": True, "_render_opacity_scale": 1.0})
    assert not _atom_render_visible({"_render_visible": True, "_render_opacity_scale": 0.0})
    assert not _atom_render_visible({"_render_visible": True, "_render_opacity_scale": -1.0})
    assert not _atom_render_visible({"_render_visible": True, "_render_opacity_scale": np.nan})
