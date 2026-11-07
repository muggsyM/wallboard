from fastapi.testclient import TestClient

from wallboard.app import create_app


def test_api_docs_disabled(data_dir):
    c = TestClient(create_app(data_dir))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404, path


def test_healthz_and_store(data_dir):
    app = create_app(data_dir)
    assert app.state.store.config.language == "cs"
    assert TestClient(app).get("/healthz").json() == {"ok": True}
