from fastapi.testclient import TestClient

from wallboard.app import create_app


def test_healthz_and_store(data_dir):
    app = create_app(data_dir)
    assert app.state.store.config.language == "cs"
    assert TestClient(app).get("/healthz").json() == {"ok": True}
