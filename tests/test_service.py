"""业务语义：注册即抓取、幂等、退订、探测两问、限流。"""

from __future__ import annotations

import pytest

from adp_registry import card as cardlib
from adp_registry.service import AdpError, Config, RegistryService, public_record
from conftest import PUBLIC_URL, make_card


def test_register_fetches_and_stores(service: RegistryService, fetcher):
    record, token, created = service.register(PUBLIC_URL)

    assert created is True
    assert token is not None and len(token) > 20
    assert fetcher.calls == [PUBLIC_URL]  # 注册即抓取（不变式 I3）
    assert record["status"] == "ok"
    assert record["failures"] == 0
    assert record["card"] == make_card()
    assert record["cardHash"] == cardlib.card_hash(make_card())
    assert record["id"] == cardlib.derive_id(PUBLIC_URL)


def test_register_is_idempotent_and_does_not_rotate_token(service: RegistryService):
    first, token, created = service.register(PUBLIC_URL)
    second, second_token, second_created = service.register(PUBLIC_URL)

    assert created is True and second_created is False
    assert second_token is None  # 不轮换：否则任何人都能靠再注册一次劫持条目
    assert first["id"] == second["id"]
    assert service.store.count() == 1


def test_register_rejects_bad_url(service: RegistryService):
    with pytest.raises(AdpError) as excinfo:
        service.register("http://geo.example.com/card.json")
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "BAD_URL"


def test_register_fails_when_card_unreachable(service: RegistryService):
    with pytest.raises(AdpError) as excinfo:
        service.register("https://down.example.com/card.json")
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "CARD_UNREACHABLE"


def test_register_fails_when_not_an_agent_card(service: RegistryService, fetcher):
    fetcher.serve(PUBLIC_URL, {"name": "only a name"})
    with pytest.raises(AdpError) as excinfo:
        service.register(PUBLIC_URL)
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "NOT_AN_AGENT_CARD"
    assert excinfo.value.detail["errors"]


def test_domain_limit(store, fetcher, clock):
    service = RegistryService(store, fetcher, Config(max_agents_per_domain=1), clock=clock.now)
    fetcher.serve("https://geo.example.com/second.json", make_card())
    service.register(PUBLIC_URL)

    with pytest.raises(AdpError) as excinfo:
        service.register("https://geo.example.com/second.json")
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail["reason"] == "domain-limit"


def test_ip_rate_limit(store, fetcher, clock):
    service = RegistryService(
        store, fetcher, Config(registrations_per_ip_per_hour=2), clock=clock.now
    )
    for index in range(2):
        url = f"https://s{index}.example.com/card.json"
        fetcher.serve(url, make_card())
        service.register(url, client_ip="203.0.113.7")

    with pytest.raises(AdpError) as excinfo:
        service.register(PUBLIC_URL, client_ip="203.0.113.7")
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail["reason"] == "ip-rate-limit"


def test_delete_requires_the_exact_token(service: RegistryService):
    record, token, _ = service.register(PUBLIC_URL)

    with pytest.raises(AdpError) as excinfo:
        service.delete_agent(record["id"], "wrong-token")
    assert excinfo.value.status_code == 401

    with pytest.raises(AdpError) as excinfo:
        service.delete_agent(record["id"], None)
    assert excinfo.value.status_code == 401

    service.delete_agent(record["id"], token)
    assert service.store.count() == 0
    with pytest.raises(AdpError) as excinfo:
        service.delete_agent(record["id"], token)
    assert excinfo.value.status_code == 404


def test_probe_success_is_idempotent(service: RegistryService):
    record, _, _ = service.register(PUBLIC_URL)
    probed = service.probe(record)

    assert probed["status"] == "ok"
    assert probed["failures"] == 0
    assert probed["changedAt"] is None  # 内容没变 → 不动 changedAt
    assert probed["prevHash"] is None
    assert probed["cardHash"] == record["cardHash"]


def test_probe_follows_card_changes(service: RegistryService, fetcher):
    record, _, _ = service.register(PUBLIC_URL)
    original_hash = record["cardHash"]

    updated_card = make_card(description="Now with routing")
    fetcher.serve(PUBLIC_URL, updated_card)
    probed = service.probe(record)

    assert probed["card"] == updated_card  # 自动跟随
    assert probed["cardHash"] == cardlib.card_hash(updated_card)
    assert probed["prevHash"] == original_hash
    assert probed["changedAt"] is not None
    # 落盘一致：card 与 cardHash 不能不同步
    stored = service.store.get(record["id"])
    assert cardlib.card_hash(stored["card"]) == stored["cardHash"]


def test_probe_marks_down_after_threshold(service: RegistryService, fetcher):
    record, _, _ = service.register(PUBLIC_URL)
    fetcher.fail(PUBLIC_URL)

    first = service.probe(record)
    assert first["failures"] == 1 and first["status"] == "ok"  # 还没到阈值

    second = service.probe(first)
    assert second["failures"] == 2 and second["status"] == "ok"

    third = service.probe(second)
    assert third["failures"] == 3 and third["status"] == "down"

    fetcher.serve(PUBLIC_URL, make_card())
    recovered = service.probe(third)
    assert recovered["status"] == "ok" and recovered["failures"] == 0


def test_probe_treats_invalid_card_as_failure(service: RegistryService, fetcher):
    record, _, _ = service.register(PUBLIC_URL)
    fetcher.serve(PUBLIC_URL, {"name": "broken"})

    probed = service.probe(record)
    assert probed["failures"] == 1


def test_backoff_grows_and_is_capped(store, fetcher, clock):
    config = Config(backoff_base_seconds=60, backoff_max_seconds=180, failure_threshold=3)
    service = RegistryService(store, fetcher, config, clock=clock.now)
    record, _, _ = service.register(PUBLIC_URL)
    fetcher.fail(PUBLIC_URL)

    current = record
    seen = []
    for _ in range(5):
        current = service.probe(current)
        seen.append(current["_nextProbeAt"])

    assert seen == sorted(seen)
    assert len(set(seen)) >= 3  # 逐次增长后到顶
    assert seen[0] < seen[-1]


def test_list_agents_filters_and_summary(service: RegistryService):
    service.register(PUBLIC_URL)
    items, cursor = service.list_agents(q="geocode")
    assert cursor is None
    summary = public_record(items[0], full=False)
    assert summary["name"] == "Geo Skills"
    assert summary["skills"] == ["geocode", "reverse"]
    assert "card" not in summary  # 默认轻量
    assert public_record(items[0], full=True)["card"] == make_card()


def test_list_agents_rejects_bad_arguments(service: RegistryService):
    for kwargs, code in (
        ({"status": "weird"}, "BAD_STATUS"),
        ({"limit": 0}, "BAD_LIMIT"),
        ({"limit": 101}, "BAD_LIMIT"),
        ({"changed_since": "not-a-time"}, "BAD_TIMESTAMP"),
        ({"cursor": "!!!not-base64!!!"}, "BAD_CURSOR"),
    ):
        with pytest.raises(AdpError) as excinfo:
            service.list_agents(**kwargs)
        assert excinfo.value.code == code


def test_registry_card_shape(service: RegistryService):
    card = service.registry_card()
    assert card["apiBase"].endswith("/v1")
    assert card["limits"]["maxCardBytes"] == Config.max_card_bytes
    assert card["probeIntervalSeconds"] == Config.probe_interval_seconds
