def test_history_requires_auth(client):
    response = client.get("/history")
    assert response.status_code == 401
