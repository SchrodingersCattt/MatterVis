"""Scientific contract tests for continuous per-atom colours."""

from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.properties import (
    AtomPropertyColorSpec,
    PropertyCatalog,
    PropertyDescriptor,
    build_color_lut,
    map_property_colors,
    normalize_property_values,
    reduce_property_values,
    resolve_property_scale,
)


def test_scalar_and_vector_auto_reductions() -> None:
    scalar, scalar_mode = reduce_property_values([1.0, 2.0], reduction="auto")
    vector, vector_mode = reduce_property_values(
        [[3.0, 4.0, 0.0], [0.0, 0.0, 2.0]], reduction="auto"
    )

    assert scalar_mode == "scalar"
    assert scalar.tolist() == [1.0, 2.0]
    assert vector_mode == "magnitude"
    assert vector.tolist() == [5.0, 2.0]


def test_tensor_requires_explicit_reduction_and_six_component_order() -> None:
    values = np.arange(12.0).reshape(2, 6)
    with pytest.raises(ValueError, match="tensor-like"):
        reduce_property_values(values, reduction="auto")
    with pytest.raises(ValueError, match="requires components"):
        reduce_property_values(values, reduction="trace")

    trace, mode = reduce_property_values(
        values,
        reduction="trace",
        components=("xx", "yy", "zz", "yz", "xz", "xy"),
    )
    assert mode == "trace"
    assert trace.tolist() == [3.0, 21.0]


def test_component_by_name_and_von_mises() -> None:
    stress = np.asarray([[4.0, 1.0, -2.0, 3.0, 2.0, 1.0]])
    order = ("xx", "yy", "zz", "yz", "xz", "xy")
    component, _ = reduce_property_values(
        stress, reduction="component", component="yz", components=order
    )
    von_mises, _ = reduce_property_values(
        stress, reduction="von_mises", components=order
    )
    assert component.tolist() == [3.0]
    assert von_mises[0] == pytest.approx(np.sqrt(69.0))


def test_exact_global_range_constant_and_centered_normalization() -> None:
    spec = AtomPropertyColorSpec(fields=("array:q",), center=0.0)
    scale = resolve_property_scale(
        [np.asarray([-2.0, np.nan]), np.asarray([1.0, np.inf])], spec
    )
    assert scale.value_range == (-2.0, 1.0)
    assert scale.finite_count == 2
    assert scale.missing_count == 2
    assert normalize_property_values(
        [-2.0, 0.0, 1.0], value_range=scale.value_range, center=0.0
    ).tolist() == [0.0, 0.5, 1.0]
    constant = normalize_property_values([3.0], value_range=(3.0, 3.0))
    assert constant.tolist() == [0.5]


def test_explicit_range_clips_and_missing_color_is_used() -> None:
    spec = AtomPropertyColorSpec(
        fields=("array:q",), value_range=(0.0, 1.0), nan_color="#010203"
    )
    scale = resolve_property_scale([[-9.0, 9.0, np.nan]], spec)
    colors = map_property_colors([-1.0, 2.0, np.nan], scale, nan_color=spec.nan_color)
    assert np.array_equal(colors[0], scale.lut[0])
    assert np.array_equal(colors[1], scale.lut[-1])
    assert colors[2].tolist() == [1, 2, 3, 255]


def test_all_nonfinite_fails() -> None:
    spec = AtomPropertyColorSpec(fields=("array:q",))
    with pytest.raises(ValueError, match="no finite"):
        resolve_property_scale([[np.nan, np.inf]], spec)


def test_lut_is_deterministic_and_invalid_colormap_fails() -> None:
    assert np.array_equal(build_color_lut("viridis"), build_color_lut("viridis"))
    with pytest.raises(ValueError, match="unknown matplotlib colormap"):
        build_color_lut("mattervis-no-such-map")


def test_unqualified_field_requires_unique_source() -> None:
    catalog = PropertyCatalog(
        (
            PropertyDescriptor("array:q", "array", "q", "float64"),
            PropertyDescriptor("sidecar:q", "sidecar", "q", "float32"),
        )
    )
    with pytest.raises(ValueError, match="ambiguous"):
        catalog.resolve("q")
    assert catalog.resolve("array:q").source == "array"
