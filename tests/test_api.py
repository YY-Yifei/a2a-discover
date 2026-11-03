"""HTTP 层端到端：5 个操作的行为、状态码与错误码。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from adp_registry import card as cardlib
from adp_registry.api import create_app
from adp_registry.service import Config, RegistryService
from conftest import PUBLIC_URL, make_card


@pytest.fixture()
def client(service: RegistryService) -> TestClient:
    return TestClient(create_app(service, probe=False))


@pytest.fixture()
def registered(client: TestClient) -> dict:
    response = client.post("/v1/agents", json={"url": PUBLIC_URL})
    assert response.status_code == 201
    return response.json()


def test_registry_card_is_served(client: TestClient):
    response = client.get("/.well-known/a2a-registry.json")
    assert response.status_code == 200
    body = response.json()
    assert body["apiBase"].endswith("/v1")
    assert body["probeIntervalSeconds"] == 900


def test_register_returns_201_with_location_and_token(client: TestClient):
    response = client.post("/v1/agents", json={"url": PUBLIC_URL})
    body = response.json()

    assert response.status_code == 201
    assert response.headers["location"] == f"/v1/agents/{body['id']}"
    assert body["manageToken"]
    assert body["status"] == "ok"
    assert body["card"] == make_card()
    assert body["cardHash"] == cardlib.card_hash(make_card())


def test_register_is_idempotent(client: TestClient, registered: dict):
    response = client.post("/v1/agents", json={"url": PUBLIC_URL})
    assert response.status_code == 200
    assert "manageToken" not in response.json()
    assert response.json()["id"] == registered["id"]


def test_register_rejects_bad_url_and_body(client: TestClient):
    bad_url = client.post("/v1/agents", json={"url": "http://geo.example.com/card.json"})
    assert bad_url.status_code == 400
    assert bad_url.json()["code"] == "BAD_URL"

    bad_body = client.post("/v1/agents", json={"nope": 1})
    assert bad_body.status_code == 400
    assert bad_body.json()["code"] == "BAD_BODY"


def test_register_returns_422_for_unverifiable_card(client: TestClient, service: RegistryService):
    response = client.post("/v1/agents", json={"url": "https://down.example.com/card.json"})
    assert response.status_code == 422
    assert response.json()["code"] == "CARD_UNREACHABLE"


def test_register_rate_limited(store, fetcher, clock):
    service = RegistryService(
        store, fetcher, Config(registrations_per_ip_per_hour=1), clock=clock.now
    )
    fetcher.serve("https://other.example.com/card.json", make_card())
    client = TestClient(create_app(service, probe=False))

    assert client.post("/v1/agents", json={"url": PUBLIC_URL}).status_code == 201
    response = client.post("/v1/agents", json={"url": "https://other.example.com/card.json"})
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"


def test_list_returns_summaries_and_paginates(client: TestClient, registered: dict):
    first = client.get("/v1/agents")
    assert first.status_code == 200
    items = first.json()["items"]
    assert len(items) == 1
    assert "card" not in items[0]
    assert items[0]["skills"] == ["geocode", "reverse"]

    full = client.get("/v1/agents?full=true").json()["items"][0]
    assert full["card"] == make_card()
    assert "firstSeenAt" in full

    assert client.get("/v1/agents?limit=0").status_code == 400
    assert client.get("/v1/agents?status=bogus").status_code == 400


def test_get_agent_by_id(client: TestClient, registered: dict):
    response = client.get(f"/v1/agents/{registered['id']}")
    assert response.status_code == 200
    assert response.json()["card"] == make_card()

    assert client.get("/v1/agents/a_0000000000000000").status_code == 404
    assert client.get("/v1/agents/not-an-id").status_code == 404


def test_delete_requires_token(client: TestClient, registered: dict):
    agent_id = registered["id"]

    assert client.delete(f"/v1/agents/{agent_id}").status_code == 401
    assert client.delete(
        f"/v1/agents/{agent_id}", headers={"X-ADP-Token": "wrong"}
    ).status_code == 401

    ok = client.delete(f"/v1/agents/{agent_id}", headers={"X-ADP-Token": registered["manageToken"]})
    assert ok.status_code == 204

    assert client.get(f"/v1/agents/{agent_id}").status_code == 404


def test_openapi_document_matches_contract_surface(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {
        "/.well-known/a2a-registry.json",
        "/v1/agents",
        "/v1/agents/{agent_id}",
    }
