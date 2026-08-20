from fastapi.testclient import TestClient
from app.main import app

client=TestClient(app)

def test_health_process_is_alive()->None:
    response=client.get("/health")

    assert response.status_code==200
    assert response.json()=={"status":"ok"}
    assert response.headers["content-type"].startswith("application/json")

def test_health_rejects_unsupported_method()->None:
    response=client.post("/health")

    assert response.status_code==405