from __future__ import annotations

from crystal_viewer.app import create_app
from crystal_viewer.config import reload_config


def test_config_rest_get_patch_delete(monkeypatch, tmp_path):
    import crystal_viewer.api.v2_config as v2_config
    from crystal_viewer.config.loader import delete_user_config as _delete_user_config
    from crystal_viewer.config.loader import write_user_config as _write_user_config

    target = tmp_path / "config.toml"

    def write(payload):
        return _write_user_config(payload, path=target)

    def delete():
        return _delete_user_config(path=target)

    def reload(path=None):
        return reload_config(str(target) if target.exists() else "__missing_config__.toml")

    monkeypatch.setattr(v2_config, "write_user_config", write)
    monkeypatch.setattr(v2_config, "delete_user_config", delete)
    monkeypatch.setattr(v2_config, "reload_config", reload)

    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()

    assert client.get("/api/v2/config").status_code == 200

    response = client.patch(
        "/api/v2/config",
        json={"style": {"atom_scale": 1.4}, "colors": {"selection_highlight": "#ABCDEF"}},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["config"]["style"]["atom_scale"] == 1.4
    assert body["config"]["colors"]["selection_highlight"] == "#ABCDEF"

    response = client.get("/api/v2/config/colors/elements")
    assert response.status_code == 200
    assert response.get_json()["elements"]["C"] == "#5E5E5E"

    response = client.delete("/api/v2/config")
    assert response.status_code == 200
    assert response.get_json()["deleted"] is True
    assert response.get_json()["config"]["style"]["atom_scale"] == 1.0

    reload_config("__missing_config__.toml")
