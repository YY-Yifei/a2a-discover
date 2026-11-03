"""文本层一致性不变式 —— 锁住「摘要 vs 字段定义 / 契约 vs 正文 / README vs 规范」这类漂移。

这些不是行为测试，也不测某一句话的对错。它们断言的是**几条可机检的文字不变式**：
核心断言用哪个字段、身份上界是否被写成无条件、契约的 pattern 是否接受正文宣称合法的写法、
正文列出的字段是否等于契约的 `required`。行为测试全绿与文档自相矛盾可以同时成立，
能拦住后者的只有这一层。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MD = (ROOT / "spec" / "adp-core.md").read_text(encoding="utf-8")
YAML_TEXT = (ROOT / "spec" / "adp-core.yaml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SPEC = yaml.safe_load(YAML_TEXT)

SURFACE = {"spec/adp-core.md": MD, "spec/adp-core.yaml": YAML_TEXT, "README.md": README}

SCHEMAS = SPEC["components"]["schemas"]
STAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


@pytest.mark.parametrize("name", sorted(SURFACE))
def test_core_assertion_never_uses_checked_at(name):
    """核心断言的时间点必须是 lastOkAt —— checkedAt 失败也前进，撑不住「取到了」。"""
    text = SURFACE[name]
    assert "`checkedAt` 从 `url` 取到了" not in text, (
        f"{name} 仍然用 checkedAt 表述「取到了 card」—— 失败探测后这句话系统性为假"
    )


def test_core_assertion_is_present_and_consistent():
    """三份发布面都必须把断言写成 lastOkAt，不能各说各的。"""
    for name in ("spec/adp-core.md", "spec/adp-core.yaml", "README.md"):
        assert "`lastOkAt` 从 `url` 取到了 `card`" in SURFACE[name], name


def test_changed_at_is_never_tied_to_checked_at():
    """§6.2：changedAt 必须取成功探测时刻，不能用 checkedAt。"""
    for name in ("spec/adp-core.md", "spec/adp-core.yaml"):
        assert "changedAt = checkedAt" not in SURFACE[name], name
        assert "changedAt` = checkedAt" not in SURFACE[name], name
    assert "changedAt = lastOkAt" in MD


@pytest.mark.parametrize(
    "forbidden",
    [
        "但**无法把「是谁」伪造到攻击者名下**",
        "只能污染「被发现」，无法把",
        "身份不会被转移到攻击者名下",
    ],
)
def test_no_unconditional_identity_bound(forbidden):
    """身份上界只能是条件式的 —— 这三句无条件表述必须消失。"""
    for name, text in SURFACE.items():
        assert forbidden not in text, f"{name} 仍含无条件上界：{forbidden}"


def test_identity_bound_is_stated_as_conditional_with_three_conditions():
    """三条件（签名 / 先验锚点 / 用锚点决定看哪一条）必须在发布面上写全。"""
    for name in ("spec/adp-core.md", "spec/adp-core.yaml", "README.md"):
        text = SURFACE[name]
        assert "不提供身份保证" in text, name
        assert "先验" in text, name
        assert "该看哪一条结果" in text, (
            f"{name} 漏了第三个条件（检索层注入：按结果挑选 = 无上界）"
        )


@pytest.mark.parametrize(
    "forbidden",
    ["投递语义是 **at-least-once**", "投递语义为 at-least-once"],
)
def test_no_at_least_once_claim(forbidden):
    """changedSince 是可重复读快照查询，不是投递 —— 不许声称 at-least-once。"""
    for name, text in SURFACE.items():
        assert forbidden not in text, f"{name} 仍声称 at-least-once"


def test_changed_since_says_instant_comparison():
    """带偏移的合法 date-time 与 Z 串按字符串比较会错序 —— 必须写明按时间点比较。"""
    assert "按**时间点**（instant）" in MD
    assert "MUST NOT 按字符串比较" in MD
    assert "按时间点（instant）" in YAML_TEXT


def test_no_identity_equals_entry_confusion():
    """换 URL 换的是条目与 id，不是身份（身份来自 A2A 签名）。"""
    for name, text in SURFACE.items():
        assert "换 URL = 换身份" not in text, name
    assert "换 URL = 换**条目**" in MD


def test_egress_path_rule_is_normative():
    """§7 必须有出网路径条款，且理由写成 CONNECT（DNS 由代理解析）。"""
    assert "出网路径不得重新解析目标主机名" in MD
    assert "CONNECT" in MD
    assert "sidecar" in MD
    assert "出网路径" in README


def test_non_ascii_host_is_documented_and_enforced():
    """规范说要拒、实现真的拒 —— 这类「契约 vs 实现」不一致必须当场暴露。"""
    assert "非 ASCII host MUST 被拒绝" in MD
    assert "MUST NOT** 做 IDNA 映射" in MD
    from adp_registry import netguard

    with pytest.raises(netguard.UrlError):
        netguard.normalize_url("https://例子.测试/card.json")


def test_registration_pattern_accepts_every_documented_spelling():
    """契约的 url pattern 必须接受正文宣称合法的书写变体（大写 scheme）。"""
    pattern = SCHEMAS["Registration"]["properties"]["url"]["pattern"]
    for ok in ("https://geo.example.com/card.json", "HTTPS://geo.example.com/card.json"):
        assert re.match(pattern, ok), f"契约拒绝了正文接受的写法：{ok}"
    for bad in ("http://geo.example.com/card.json", "ftp://geo.example.com/card.json"):
        assert not re.match(pattern, bad), f"契约接受了不该接受的写法：{bad}"


@pytest.mark.parametrize("schema_name", ["AgentRecord", "AgentRecordWithToken", "AgentSummary"])
def test_timestamp_pattern_matches_implementation(schema_name):
    """出参时间戳必须秒级定宽 UTC，且 pattern 与实现的 isoformat 输出吻合。"""
    from adp_registry.service import isoformat

    moment = datetime(2026, 11, 1, 8, 0, 0, 123456, tzinfo=timezone.utc)
    rendered = isoformat(moment)
    assert rendered == "2026-11-01T08:00:00Z"

    properties = SCHEMAS[schema_name]["properties"]
    stamped = [
        field
        for field in ("firstSeenAt", "checkedAt", "lastOkAt", "changedAt")
        if field in properties
    ]
    assert stamped, f"{schema_name} 没有任何时间字段"
    for field in stamped:
        pattern = properties[field]["pattern"]
        assert pattern == STAMP_PATTERN, f"{schema_name}.{field} 缺秒级定宽 pattern"
        assert re.match(pattern, rendered), f"{schema_name}.{field} pattern 与实现不符"


def test_agent_summary_doc_matches_contract_required():
    """5.3 正文列出的 summary 字段必须与契约 required 完全一致。"""
    match = re.search(r"返回 AgentSummary：(.+?)——", MD, re.S)
    assert match, "5.3 找不到 AgentSummary 字段清单"
    documented = set(re.findall(r"`([A-Za-z]+)`", match.group(1)))
    required = set(SCHEMAS["AgentSummary"]["required"])
    assert documented == required, (
        f"正文与契约不一致：正文多 {sorted(documented - required)} / 缺 {sorted(required - documented)}"
    )


def test_published_surface_has_no_stale_proxy_wording():
    """README 不能只承诺「忽略环境代理」—— 条款管的是整条出网路径。"""
    assert "忽略环境代理" not in README
