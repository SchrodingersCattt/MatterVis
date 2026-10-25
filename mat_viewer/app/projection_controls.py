"""Scene-scoped projection radio reconciliation for the Dash callback owner."""

from dash import no_update

from .camera_helpers import _coerce_projection


def triggered_component_ids(context):
    """Collect a whole trigger batch, including a simultaneous tab change."""
    triggered_ids = {
        str(item["prop_id"]).split(".")[0]
        for item in getattr(context, "triggered", None) or []
        if isinstance(item, dict) and item.get("prop_id")
    }
    return triggered_ids or {getattr(context, "triggered_id", None)}


def selected_scene_projection(backend, state, scene_tab, *, tab_changed):
    """Read the selected scene's live projection, fencing unrelated stores."""
    if not scene_tab or scene_tab not in backend.scene_store.scenes:
        return no_update
    if not tab_changed and (
        not isinstance(state, dict) or str(state.get("scene_id") or "") != str(scene_tab)
    ):
        return no_update
    # The payload may predate a newer camera command on the same scene.
    live = backend.get_state(scene_tab)
    return _coerce_projection(live.get("projection") or "perspective")
