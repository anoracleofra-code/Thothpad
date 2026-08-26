from fastapi.testclient import TestClient

from backend.main import app


def test_profiles_endpoint():
    client = TestClient(app)
    res = client.get("/api/profiles")
    assert res.status_code == 200
    assert any(row["name"] == "creative-default" for row in res.json())


def test_diagnose_endpoint():
    client = TestClient(app)
    res = client.post("/api/diagnose", json={"text": "It wasn't fear. It was memory.", "profile": "creative-default"})
    assert res.status_code == 200
    assert res.json()["mode"] == "diagnose"


def test_agent_setup_endpoint():
    client = TestClient(app)
    res = client.get("/api/agent-setup")
    assert res.status_code == 200
    assert "thothpad-mcp.cmd" in res.json()["mcp_command"]


def test_projects_endpoint():
    client = TestClient(app)
    res = client.post("/api/projects", json={"name": "pytest project", "profile": "creative-default"})
    assert res.status_code == 200
    assert res.json()["name"] == "pytest project"


def test_manuscript_endpoint():
    client = TestClient(app)
    res = client.post(
        "/api/manuscript",
        json={
            "profile": "creative-default",
            "documents": [
                {"name": "one.md", "text": "A dime a dozen. A dime a dozen."},
                {"name": "two.md", "text": "It became a wild goose chase."},
            ],
        },
    )
    assert res.status_code == 200
    assert res.json()["document_count"] == 2


def test_integrations_endpoint():
    client = TestClient(app)
    res = client.get("/api/integrations")
    assert res.status_code == 200
    assert "proselint" in res.json()
