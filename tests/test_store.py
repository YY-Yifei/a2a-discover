"""存储层：原样存取、固定排序、游标分页、子串检索、facet 过滤。"""

from __future__ import annotations

from adp_registry import card as cardlib
from adp_registry.store import Store
from conftest import make_card


def make_record(url: str, *, first_seen: str, status: str = "ok", card: dict | None = None, changed_at=None):
    payload = card or make_card()
    return {
        "id": cardlib.derive_id(url),
        "url": url,
        "host": url.split("/")[2],
        "card": payload,
        "cardHash": cardlib.card_hash(payload),
        "status": status,
        "firstSeenAt": first_seen,
        "checkedAt": first_seen,
        "lastOkAt": first_seen,
        "failures": 0,
        "changedAt": changed_at,
        "prevHash": None,
        "_manageTokenHash": "x" * 64,
        "_nextProbeAt": first_seen,
        "_searchBlob": cardlib.search_blob(payload),
    }


def test_insert_get_and_delete(store: Store):
    record = make_record("https://a.example.com/card.json", first_seen="2026-11-01T00:00:00Z")
    store.insert(record)

    loaded = store.get(record["id"])
    assert loaded is not None
    assert loaded["card"] == record["card"]  # 原样读回（不变式 I2）
    assert store.get_by_url(record["url"])["id"] == record["id"]
    assert store.count() == 1

    assert store.delete(record["id"]) is True
    assert store.delete(record["id"]) is False
    assert store.get(record["id"]) is None


def test_card_is_stored_verbatim(store: Store):
    """存储层不得改写 Card：含未知字段、嵌套顺序都要原样。"""
    card = make_card()
    card["x-vendor"] = {"nested": [1, 2, {"z": None}]}
    card["extraField"] = "kept"
    record = make_record(
        "https://a.example.com/card.json",
        first_seen="2026-11-01T00:00:00Z",
        card=card,
    )
    store.insert(record)
    assert store.get(record["id"])["card"] == card


def test_fixed_order_and_cursor_pagination(store: Store):
    stamps = ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z"]
    for index, stamp in enumerate(stamps):
        store.insert(
            make_record(f"https://s{index}.example.com/card.json", first_seen=stamp)
        )

    first_page, cursor = store.query(limit=2)
    assert len(first_page) == 2
    assert cursor is not None
    # 固定排序：firstSeenAt DESC, id ASC
    assert first_page[0]["firstSeenAt"] == "2026-11-02T00:00:00Z"
    assert first_page[0]["id"] < first_page[1]["id"]

    second_page, next_cursor = store.query(limit=2, cursor=cursor)
    assert len(second_page) == 1
    assert next_cursor is None
    assert {record["id"] for record in first_page + second_page} == {
        record["id"] for record in (first_page + second_page)
    }
    assert second_page[0]["firstSeenAt"] == "2026-11-01T00:00:00Z"


def test_query_filters(store: Store):
    geo = make_record("https://geo.example.com/card.json", first_seen="2026-11-01T00:00:00Z")
    other_card = make_card(
        name="Weather Bot",
        description="Forecasts and radar",
        skills=[{"id": "forecast", "name": "Forecast", "description": "7 day", "tags": ["weather"]}],
    )
    weather = make_record(
        "https://weather.example.com/card.json",
        first_seen="2026-11-02T00:00:00Z",
        card=other_card,
    )
    store.insert(geo)
    store.insert(weather)

    assert [r["id"] for r in store.query(q="forecast")[0]] == [weather["id"]]
    assert [r["id"] for r in store.query(q="GEOCODE")[0]] == [geo["id"]]  # 大小写不敏感
    assert [r["id"] for r in store.query(skill="geocode")[0]] == [geo["id"]]
    assert [r["id"] for r in store.query(tag="weather")[0]] == [weather["id"]]
    assert store.query(q="不存在的东西")[0] == []


def test_query_escapes_like_wildcards(store: Store):
    store.insert(make_record("https://geo.example.com/card.json", first_seen="2026-11-01T00:00:00Z"))
    assert store.query(q="%")[0] == []  # % 是字面量，不是通配符


def test_changed_since_filter(store: Store):
    store.insert(
        make_record(
            "https://geo.example.com/card.json",
            first_seen="2026-11-01T00:00:00Z",
            changed_at="2026-11-02T10:00:00Z",
        )
    )
    store.insert(make_record("https://b.example.com/card.json", first_seen="2026-11-01T00:00:00Z"))

    hits, _ = store.query(changed_since="2026-11-02T00:00:00Z")
    assert len(hits) == 1
    assert hits[0]["changedAt"] == "2026-11-02T10:00:00Z"
    assert store.query(changed_since="2026-12-01T00:00:00Z")[0] == []


def test_due_and_next_probe(store: Store):
    store.insert(make_record("https://a.example.com/card.json", first_seen="2026-11-01T00:00:00Z"))
    store.insert(make_record("https://b.example.com/card.json", first_seen="2026-11-05T00:00:00Z"))

    assert len(store.due("2026-11-02T00:00:00Z")) == 1
    assert len(store.due("2026-11-06T00:00:00Z")) == 2
    assert store.next_probe_at() == "2026-11-01T00:00:00Z"


def test_count_host(store: Store):
    store.insert(make_record("https://a.example.com/card.json", first_seen="2026-11-01T00:00:00Z"))
    store.insert(make_record("https://a.example.com/other.json", first_seen="2026-11-01T00:00:00Z"))
    store.insert(make_record("https://b.example.com/card.json", first_seen="2026-11-01T00:00:00Z"))
    assert store.count_host("a.example.com") == 2
    assert store.count_host("b.example.com") == 1
