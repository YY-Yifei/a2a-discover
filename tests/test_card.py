"""JCS 规范化、摘要派生、AgentCard 校验。"""

from __future__ import annotations

import pytest

from adp_registry import card as cardlib
from adp_registry import netguard
from conftest import make_card


def test_equivalent_url_variants_share_one_id():
    """spec §4.1：归一化后，同一 URL 的书写变体必须收敛到同一个 id。

    否则会出现「两个 id、同一张卡」，直接打穿「同一 url 至多一个条目」（§4.1 性质 3）。
    注意换 URL 换的是**条目**与 `id`，不是身份 —— 身份来自 A2A 签名（§3 I4）。
    """
    variants = [
        "https://Geo.Example.com:443/Card.json",
        "https://geo.example.com/Card.json",
        "HTTPS://geo.example.com/Card.json",
    ]
    ids = {cardlib.derive_id(netguard.normalize_url(v)) for v in variants}
    assert len(ids) == 1


def test_jcs_matches_rfc8785_example():
    """RFC 8785 附录给出的参考向量。"""
    sample = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27],
        "string": "€$\u000f\nA'B\"\\\\\"/",
        "literals": [None, True, False],
    }
    assert cardlib.canonical_json(sample) == (
        '{"literals":[null,true,false],'
        '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )


def test_jcs_sorts_keys_by_utf16_code_unit():
    """RFC 8785 §3.2.3：排序按 UTF-16 码元 —— 星面字符因此排在 U+FB33 之前。"""
    sample = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "\ufb33": "Hebrew",
        "1": "One",
        "\U0001f600": "Emoji",
        "é": "LATIN",
    }
    keys = [chunk.split(":")[0] for chunk in cardlib.canonical_json(sample).split('","')]
    assert keys[0].startswith('{"\\r"')
    assert cardlib.canonical_json(sample).index('"\U0001f600"') < cardlib.canonical_json(
        sample
    ).index('"\ufb33"')


def test_jcs_rejects_non_finite_numbers():
    with pytest.raises(ValueError):
        cardlib.canonical_json({"x": float("nan")})


def test_card_hash_is_key_order_independent():
    card = make_card()
    reordered = dict(reversed(list(card.items())))
    assert cardlib.card_hash(card) == cardlib.card_hash(reordered)
    assert cardlib.card_hash(card).startswith("sha256:")
    assert len(cardlib.card_hash(card)) == len("sha256:") + 64


def test_derive_id_is_deterministic():
    """本实现选用的 RECOMMENDED 派生法 —— §4.1 不要求算法，只要求 id 稳定且 URL-safe。"""
    url = "https://geo.example.com/.well-known/agent-card.json"
    assert cardlib.derive_id(url) == cardlib.derive_id(url)
    assert cardlib.derive_id(url) != cardlib.derive_id(url + "/")
    assert cardlib.ID_RE.match(cardlib.derive_id(url))


def test_validate_card_accepts_minimal_card():
    assert cardlib.validate_card(make_card()) == []


def test_validate_card_reports_every_problem():
    broken = {
        "name": "",
        "version": "1.0.0",
        "supportedInterfaces": [],
        "capabilities": "not-an-object",
        "defaultInputModes": [],
        "skills": [
            {"id": "", "name": ""},
            {"id": "dup", "name": "A", "tags": "not-a-list"},
            {"id": "dup", "name": "B"},
        ],
    }
    problems = cardlib.validate_card(broken)
    text = "\n".join(problems)
    for expected in (
        "name:",
        "description: 缺失",
        "supportedInterfaces: 必须是非空数组",
        "capabilities: 必须是对象",
        "defaultInputModes: 必须是非空字符串数组",
        "defaultOutputModes: 缺失",
        "skills[0].id",
        "skills[2].id: 重复",
        "skills[1].tags",
    ):
        assert expected in text


def test_validate_card_rejects_non_object():
    assert cardlib.validate_card(["not", "a", "card"]) == ["card: 必须是 JSON 对象"]


def test_summarize_and_search_blob():
    summary = cardlib.summarize(make_card())
    assert summary["name"] == "Geo Skills"
    assert summary["skills"] == ["geocode", "reverse"]
    assert summary["tags"] == ["geo", "maps"]

    blob = cardlib.search_blob(make_card())
    assert "geocode" in blob  # 小写化后的子串检索面
    assert blob == blob.casefold()
