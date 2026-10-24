"""Catalog snapshots outlive real native close/Undo and reload."""
from test_revision_scenes_web import app, CIF, rev  # noqa: F401

from mat_viewer.extensions import StructureCatalog


def test_catalog_close_undo_reload_reopen(app):
    context = app.extension_context
    catalog = StructureCatalog(context.revisions)
    original = catalog.add(rev("input"), CIF, label="Input")
    derived = catalog.add(rev("derived"), CIF, label="Derived", parent=original.id,
                          operation="identity", checks={"unchanged": True})
    binding = catalog.open(original.id).result(30)
    app.crystal_backend.delete_scene(binding.scene.scene_id)
    app.crystal_backend.undo_scene_close()
    reopened = catalog.open(original.id).result(30)
    assert reopened.scene != binding.scene
    target = app.crystal_backend._extension_structures.new_target()
    catalog.focus(original.id, target=target).result(30)
    catalog.reload(derived.id, reopened.scene).result(30)
    final = catalog.open(original.id).result(30)
    assert final.scene != reopened.scene
    assert catalog.open(original.id).result(30) == final
    assert catalog.entries() == (original, derived)
    assert original.source == CIF.read_bytes()
