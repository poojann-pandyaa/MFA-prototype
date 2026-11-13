"""The four pre-registered customers: sign in, have history, and their authenticators can be switched."""
from app import seed
from app.db import SessionLocal


def login(client, name):
    r = client.post("/auth/login", json={"username": name, "password": "demo1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Device-Id": "dev1"}


def test_customers_have_profile_history_and_can_sign_in(client):
    with SessionLocal() as db:
        seed.seed_customers(db)
    h = login(client, "ramesh")
    me = client.get("/me", headers=h).json()
    assert me["profile"]["age"] == 68 and me["profile"]["account_number"].startswith("5010")
    rows = client.get("/pay", headers=h).json()
    assert len(rows) == 14 and rows[0]["payee_name"]
    assert client.get("/demo/users").json()["users"][0]["username"] == "asha"


def test_switching_off_an_authenticator_changes_the_plan(client, stub):
    with SessionLocal() as db:
        seed.seed_customers(db)
    h = login(client, "asha")
    stub("HIGH", "PAYEE_RISK")
    body = {"payee_vpa": "refund.desk@ybl", "amount": 4000}
    before = [s["method"] for s in client.post("/pay/risk", json=body, headers=h).json()["plan"]["steps"]]
    for m in before:
        client.post("/demo/methods", json={"method": m, "enrolled": False}, headers=h)
    after = [s["method"] for s in client.post("/pay/risk", json=body, headers=h).json()["plan"]["steps"]]
    assert before and set(before).isdisjoint(after)
