"""ADP 契约自检：解析 ``spec/adp-core.yaml``，校验结构、$ref 可解析、以及
「契约面不得膨胀」三条硬约束。

用法：``python spec/validate.py``（退出码 0 = 通过，可直接进 CI）
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent

OPERATIONS = {
    ("/.well-known/a2a-registry.json", "get"),
    ("/v1/agents", "post"),
    ("/v1/agents", "get"),
    ("/v1/agents/{id}", "get"),
    ("/v1/agents/{id}", "delete"),
}
RECORD_REQUIRED = {
    "id",
    "url",
    "card",
    "cardHash",
    "status",
    "firstSeenAt",
    "checkedAt",
    "lastOkAt",
    "failures",
}
FORBIDDEN_RECORD_FIELDS = {
    "trust",
    "health",
    "score",
    "visibility",
    "attestation",
    "etag",
    "rank",
    "verified",
}


def _refs(node: object, acc: list[str] | None = None) -> list[str]:
    """递归收集所有本地 $ref 指针。"""
    if acc is None:
        acc = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                acc.append(value)
            else:
                _refs(value, acc)
    elif isinstance(node, list):
        for value in node:
            _refs(value, acc)
    return acc


def _resolve(doc: dict, ref: str) -> object:
    if not ref.startswith("#/"):
        raise ValueError(f"只支持本地引用：{ref}")
    current: object = doc
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"无法解析 {ref}（缺 {part}）")
        current = current[part]
    return current


def check(doc: dict) -> list[str]:
    problems: list[str] = []

    if not str(doc.get("openapi", "")).startswith("3.1"):
        problems.append(f"openapi 版本应为 3.1.x，实际 {doc.get('openapi')!r}")
    for key in ("info", "paths", "components"):
        if key not in doc:
            problems.append(f"缺少顶层键 {key}")

    paths = doc.get("paths", {})
    operations = [
        (path, method)
        for path, item in paths.items()
        for method in item
        if method in ("get", "post", "put", "patch", "delete", "head", "options")
    ]
    for path, method in operations:
        operation = paths[path][method]
        if "operationId" not in operation:
            problems.append(f"{method.upper()} {path} 缺 operationId")
        if "responses" not in operation:
            problems.append(f"{method.upper()} {path} 缺 responses")

    if set(operations) != OPERATIONS:
        extra = sorted(set(operations) - OPERATIONS)
        missing = sorted(OPERATIONS - set(operations))
        problems.append(f"操作集合不符：多 {extra} 少 {missing}")

    for ref in sorted(set(_refs(doc))):
        try:
            _resolve(doc, ref)
        except (KeyError, ValueError) as exc:
            problems.append(str(exc))

    identifiers = [paths[path][method]["operationId"] for path, method in operations]
    duplicated = {name for name in identifiers if identifiers.count(name) > 1}
    if duplicated:
        problems.append(f"operationId 重复：{sorted(duplicated)}")

    schemas = doc.get("components", {}).get("schemas", {})
    record = schemas.get("AgentRecord", {})
    missing_required = RECORD_REQUIRED - set(record.get("required", []))
    if missing_required:
        problems.append(f"AgentRecord 缺必填字段：{sorted(missing_required)}")
    forbidden = set(record.get("properties", {})) & FORBIDDEN_RECORD_FIELDS
    if forbidden:
        problems.append(f"AgentRecord 混入了评价性字段：{sorted(forbidden)}")
    status_enum = record.get("properties", {}).get("status", {}).get("enum")
    if status_enum != ["ok", "down"]:
        problems.append(f"status 应只有 ok/down，实际 {status_enum!r}")

    # id 的生成算法由实现自选（§4.1），契约只约束形状。这里查两件事：
    # ① 三处 id pattern 必须一致；② RECOMMENDED 派生法与 ULID 形态都必须落在 pattern 内
    #    —— 否则契约会把参考实现自己的合法产出、或另一种合法选择判成非法。
    id_patterns = {
        schemas[name]["properties"]["id"]["pattern"]
        for name in ("AgentRecord", "AgentRecordWithToken", "AgentSummary")
        if "id" in schemas.get(name, {}).get("properties", {})
    }
    if len(id_patterns) != 1:
        problems.append(f"id pattern 在 schema 间不一致：{sorted(id_patterns)}")
    else:
        id_pattern = id_patterns.pop()
        sample_url = "https://geo.example.com/.well-known/agent-card.json"
        sample_id = "a_" + hashlib.sha256(sample_url.encode()).hexdigest()[:16]
        if not re.fullmatch(id_pattern, sample_id):
            problems.append(f"id pattern 拒绝了 RECOMMENDED 派生法的产出：{sample_id}")
        if not re.fullmatch(id_pattern, "01ARZ3NDEKTSV4RRFFQ69G5FAV"):
            problems.append("id pattern 拒绝了 ULID 形态 —— 算法应由实现选择（§4.1）")

    return problems


def main() -> int:
    spec_path = HERE / "adp-core.yaml"
    with spec_path.open("r", encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)

    problems = check(doc)

    paths = doc.get("paths", {})
    operations = [
        (path, method)
        for path, item in paths.items()
        for method in item
        if method in ("get", "post", "put", "patch", "delete", "head", "options")
    ]
    schemas = doc.get("components", {}).get("schemas", {})
    refs = _refs(doc)

    print(f"文件      : {spec_path.name}")
    print(f"paths     : {len(paths)}（操作 {len(operations)}）")
    print(f"schemas   : {len(schemas)}")
    print(f"$ref      : {len(refs)} 处 / {len(set(refs))} 个唯一")

    if problems:
        print("\n[FAIL]")
        for problem in problems:
            print("  - " + problem)
        return 1

    print("\n[OK] 契约自洽：$ref 全可解析、无重复 operationId、操作集合未膨胀。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
