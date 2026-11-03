"""AgentCard 的规范化、摘要与合规校验。

ADP Core 的两条不变式都落在这里：

* I2 —— Card 的字段与字段值原样保存，因此本模块只读不写（cardHash 是语义摘要，不要求逐字节复现源站报文）；
* I4 —— ``cardHash`` 必须由 Consumer 可独立重算，因此规范化必须走 RFC 8785 (JCS)，
  与 A2A 签名使用的同一套规则。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

ID_RE = re.compile(r"^a_[0-9a-f]{16}$")
CARD_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

ID_PREFIX = "a_"
HASH_PREFIX = "sha256:"


# --------------------------------------------------------------------------- JCS


def _utf16_key(name: str) -> bytes:
    """RFC 8785 要求键按 UTF-16 码元排序（不是按码点）。"""
    return name.encode("utf-16-be")


def _number(value: float) -> str:
    """ECMAScript Number::toString 的近似实现（JCS §3.2.2.3）。"""
    if value != value or math.isinf(value):  # NaN / Infinity
        raise ValueError("JCS 不允许 NaN / Infinity")
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)  # Python 的 repr 是最短往返表示，与 ES 的位数选择一致
    match = re.fullmatch(r"(-?)(\d+\.\d+|\d+)[eE]([+-]?\d+)", text)
    if match:
        sign, mantissa, exponent = match.groups()
        return f"{sign}{mantissa}e{'+' if int(exponent) > 0 else '-'}{abs(int(exponent))}"
    return text


def _canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    if isinstance(value, Mapping):
        keys = sorted((str(key) for key in value), key=_utf16_key)
        pairs = ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + _canonical(value[key])
            for key in keys
        )
        return "{" + pairs + "}"
    raise TypeError(f"无法规范化的类型：{type(value)!r}")


def canonical_json(value: Any) -> str:
    """返回 RFC 8785 规范化后的 JSON 文本（无空白、键有序、数字按 ES 规则）。"""
    return _canonical(value)


# ----------------------------------------------------------------------- 派生规则


def card_hash(card: Mapping[str, Any]) -> str:
    """``sha256:`` + hex(sha256(JCS(card)))。"""
    digest = hashlib.sha256(canonical_json(card).encode("utf-8")).hexdigest()
    return HASH_PREFIX + digest


def derive_id(url: str) -> str:
    """``a_`` + sha256(url) 前 16 个 hex 字符 —— 确定性派生，因此重复注册天然幂等。

    调用方 MUST 先经 ``netguard.normalize_url`` 归一（spec §4.1），否则同一 URL 的
    书写变体（host 大小写、默认端口、百分号编码）会派生出不同 id，出现「两个 id、
    同一张卡」。本函数刻意不做归一：它必须是纯函数，归一规则只应有一处定义。
    """
    return ID_PREFIX + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------------- 校验


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_str_list(value: Any, *, allow_empty: bool) -> bool:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return False
    return allow_empty or bool(value)


def validate_card(card: Any) -> list[str]:
    """返回全部校验失败项（字段路径 + 原因）；空列表 = 合法 AgentCard。"""
    problems: list[str] = []
    if not isinstance(card, dict):
        return ["card: 必须是 JSON 对象"]

    for field in ("name", "description", "version"):
        if field not in card:
            problems.append(f"{field}: 缺失")
        elif field == "description":
            if not isinstance(card[field], str):
                problems.append(f"{field}: 必须是字符串")
        elif not _is_nonempty_str(card[field]):
            problems.append(f"{field}: 必须是非空字符串")

    interfaces = card.get("supportedInterfaces")
    if interfaces is None:
        problems.append("supportedInterfaces: 缺失")
    elif not isinstance(interfaces, list) or not interfaces:
        problems.append("supportedInterfaces: 必须是非空数组")
    else:
        for index, interface in enumerate(interfaces):
            where = f"supportedInterfaces[{index}]"
            if not isinstance(interface, dict):
                problems.append(f"{where}: 必须是对象")
                continue
            if not _is_nonempty_str(interface.get("url")):
                problems.append(f"{where}.url: 必须是非空字符串")
            if not _is_nonempty_str(interface.get("protocolBinding")):
                problems.append(f"{where}.protocolBinding: 必须是非空字符串")
            if "protocolVersion" in interface and not isinstance(
                interface["protocolVersion"], str
            ):
                problems.append(f"{where}.protocolVersion: 必须是字符串")

    if not isinstance(card.get("capabilities"), dict):
        problems.append("capabilities: 必须是对象")

    for field in ("defaultInputModes", "defaultOutputModes"):
        if field not in card:
            problems.append(f"{field}: 缺失")
        elif not _is_str_list(card[field], allow_empty=False):
            problems.append(f"{field}: 必须是非空字符串数组")

    skills = card.get("skills")
    if skills is None:
        problems.append("skills: 缺失")
    elif not isinstance(skills, list):
        problems.append("skills: 必须是数组")
    else:
        seen: set[str] = set()
        for index, skill in enumerate(skills):
            where = f"skills[{index}]"
            if not isinstance(skill, dict):
                problems.append(f"{where}: 必须是对象")
                continue
            skill_id = skill.get("id")
            if not _is_nonempty_str(skill_id):
                problems.append(f"{where}.id: 必须是非空字符串")
            elif skill_id in seen:
                problems.append(f"{where}.id: 重复（{skill_id}）")
            else:
                seen.add(skill_id)
            if not _is_nonempty_str(skill.get("name")):
                problems.append(f"{where}.name: 必须是非空字符串")
            if "tags" in skill and not _is_str_list(skill["tags"], allow_empty=True):
                problems.append(f"{where}.tags: 必须是字符串数组")

    if "signatures" in card and not isinstance(card["signatures"], list):
        problems.append("signatures: 必须是数组")

    return problems


def summarize(card: Mapping[str, Any]) -> dict[str, Any]:
    """把 Card 压成检索用的 summary（默认列表视图）。"""
    skills = card.get("skills")
    skill_ids: list[str] = []
    tags: set[str] = set()
    if isinstance(skills, list):
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            if isinstance(skill.get("id"), str):
                skill_ids.append(skill["id"])
            for tag in skill.get("tags") or []:
                if isinstance(tag, str):
                    tags.add(tag)
    return {
        "name": card.get("name") or "",
        "description": card.get("description") or "",
        "skills": skill_ids,
        "tags": sorted(tags),
    }


def search_blob(card: Mapping[str, Any]) -> str:
    """检索用文本（子串匹配的目标），只包含 Card 上已有的字段。"""
    parts = [str(card.get("name") or ""), str(card.get("description") or "")]
    skills = card.get("skills")
    if isinstance(skills, list):
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            parts.append(str(skill.get("name") or ""))
            parts.append(str(skill.get("description") or ""))
    return "\n".join(part for part in parts if part).casefold()
