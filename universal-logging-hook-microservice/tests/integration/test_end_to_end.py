import pytest
from fastapi.testclient import TestClient
from src.main import app  # Assuming main imports everything

@pytest.fixture
def client():
    return TestClient(app)

def test_full_log_flow(client):
    # Send log, create checkpoint, replay
    # Assumes services running; use docker-compose in CI
    response = client.post("/logs", json={"level": "info", "message": "e2e test", "source": "app"}, headers={"X-API-KEY": "your_secret_key"})
    assert response.status_code == 200

    checkpoint_resp = client.post("/checkpoint", headers={"X-API-KEY": "your_secret_key"})
    assert checkpoint_resp.status_code == 200
    checkpoint_id = checkpoint_resp.json()["checkpoint_id"]

    replay_resp = client.get(f"/replay/{checkpoint_id}", headers={"X-API-KEY": "your_secret_key"})
    assert replay_resp.status_code == 200
    assert "logs" in replay_resp.json()  