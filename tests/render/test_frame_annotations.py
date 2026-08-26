from __future__ import annotations

from types import SimpleNamespace

import pytest

from mat_viewer.render.frame_annotations import (
    FrameAnnotationSpec,
    FrameFieldSpec,
    parse_frame_field,
    resolve_frame_annotations,
)


def _frame(index: int, **info):
    return SimpleNamespace(index=index, info=info)


def test_parse_frame_field_preserves_semantics_and_transform() -> None:
    field = parse_frame_field(
        "angle=metadata:rotation_deg,role=observable,unit=deg," "scale=2,offset=-1"
    )

    assert field == FrameFieldSpec(
        name="angle",
        source="metadata:rotation_deg",
        role="observable",
        unit="deg",
        scale=2.0,
        offset=-1.0,
    )


def test_metadata_linear_index_and_stage_share_one_template() -> None:
    frames = (
        _frame(1, rotation_deg=12.5, stage="climb"),
        _frame(3, rotation_deg=44.0, stage="climb"),
    )
    series = resolve_frame_annotations(
        frames,
        FrameAnnotationSpec(
            fields=(
                FrameFieldSpec("image", "index", role="progress"),
                FrameFieldSpec("lambda", "linear:0:0.25", role="progress"),
                FrameFieldSpec(
                    "angle",
                    "metadata:rotation_deg",
                    role="observable",
                    unit="deg",
                ),
                FrameFieldSpec("stage", "metadata:stage", role="stage"),
            ),
            template=(
                "image={image}  lambda={lambda:.2f}  "
                "rotation={angle:.1f} deg  {stage}"
            ),
        ),
    )

    assert series.labels == (
        "image=1  lambda=0.25  rotation=12.5 deg  climb",
        "image=3  lambda=0.75  rotation=44.0 deg  climb",
    )
    assert series.fields[0].values == (1, 3)
    assert series.fields[1].values == pytest.approx((0.25, 0.75))
    assert series.fields[3].values == ("climb", "climb")
    assert series.fields[2].provenance == {
        "kind": "frame_metadata",
        "key": "rotation_deg",
    }
    assert series.to_metadata()["fields"][3]["role"] == "stage"


def test_table_values_follow_source_frame_indices_and_record_hash(tmp_path) -> None:
    table = tmp_path / "neb.csv"
    table.write_text(
        "energy,rotation_deg\n0.0,0\n0.1,15\n0.2,30\n0.3,45\n",
        encoding="utf-8",
    )
    series = resolve_frame_annotations(
        (_frame(1), _frame(3)),
        FrameAnnotationSpec(
            fields=(
                FrameFieldSpec(
                    "angle",
                    f"table:{table}:rotation_deg",
                    unit="deg",
                ),
            ),
            template="rotation={angle:.0f} deg",
        ),
    )

    assert series.labels == ("rotation=15 deg", "rotation=45 deg")
    provenance = series.fields[0].provenance
    assert provenance["path"] == str(table.resolve())
    assert provenance["column"] == "rotation_deg"
    assert provenance["row_mapping"] == "source_frame_index"
    assert len(provenance["sha256"]) == 64


@pytest.mark.parametrize(
    ("fields", "template", "message"),
    [
        ((FrameFieldSpec("x", "index"),), "{missing}", "undefined field"),
        (
            (
                FrameFieldSpec("x", "index"),
                FrameFieldSpec("x", "metadata:x"),
            ),
            "{x}",
            "unique",
        ),
    ],
)
def test_annotation_contract_rejects_ambiguous_templates(
    fields,
    template,
    message,
) -> None:
    with pytest.raises(ValueError, match=message):
        FrameAnnotationSpec(fields=fields, template=template)


def test_missing_metadata_and_categorical_scaling_fail_loudly() -> None:
    with pytest.raises(ValueError, match="every selected frame"):
        resolve_frame_annotations(
            (_frame(0, angle=2.0), _frame(1)),
            FrameAnnotationSpec(
                fields=(FrameFieldSpec("angle", "metadata:angle"),),
                template="{angle}",
            ),
        )

    with pytest.raises(ValueError, match="categorical"):
        resolve_frame_annotations(
            (_frame(0, stage="initial"), _frame(1, stage="final")),
            FrameAnnotationSpec(
                fields=(
                    FrameFieldSpec(
                        "stage",
                        "metadata:stage",
                        role="stage",
                        scale=2.0,
                    ),
                ),
                template="{stage}",
            ),
        )
