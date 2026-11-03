"""契约一致性：规范文件自洽，且实现吐出的每个对象都符合 ``adp-core.yaml``。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from adp_registry.api import create_app
from adp_registry.service import RegistryService
from conftest import PUBLIC_URL

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "spec" / "adp-core.yaml"
VALIDATOR_PATH = ROOT / "spec" / "validate.py"


def _load_spec() -> dict:
    with SPEC_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def spec() -> dict:
    return _load_spec()


def _validator(spec: dict, schema_name: str) -> Draft202012Validator:
    # 把 components 一起塞进根，$ref 的 "#/components/..." 就能就地解析。
    root = {"components": spec["components"], "$ref": f"#/components/schemas/{schema_name}"}
    return Draft202012Validator(root)


def test_spec_file_passes_its_own_check():
    spec_module = importlib.util.spec_from_file_location("adp_validate", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec_module)
    spec_module.loader.exec_module(module)
    assert module.check(_load_spec()) == []


def test_contract_keeps_the_core_surface_minimal(spec: dict):
    """任何人想加回健康分 / 审核字段，这里会先拦住他。"""
    record = spec["components"]["schemas"]["AgentRecord"]
    assert set(record["properties"]) == {
        "id",
        "url",
        "card",
        "cardHash",
        "status",
        "firstSeenAt",
        "checkedAt",
        "lastOkAt",
        "failures",
        "changedAt",
        "prevHash",
    }
    assert record["properties"]["status"]["enum"] == ["ok", "down"]


def test_registry_card_conforms(service: RegistryService, spec: dict):
    client = TestClient(create_app(service, probe=False))
    body = client.get("/.well-known/a2a-registry.json").json()
    _validator(spec, "RegistryCard").validate(body)


def test_registration_and_records_conform(service: RegistryService, spec: dict):
    client = TestClient(create_app(service, probe=False))

    created = client.post("/v1/agents", json={"url": PUBLIC_URL})
    assert created.status_code == 201
    body = created.json()

    _validator(spec, "AgentRecordWithToken").validate(body)

    record_without_token = {key: value for key, value in body.items() if key != "manageToken"}
    _validator(spec, "AgentRecord").validate(record_without_token)

    single = client.get(f"/v1/agents/{body['id']}").json()
    _validator(spec, "AgentRecord").validate(single)

    listing = client.get("/v1/agents").json()
    _validator(spec, "AgentList").validate(listing)

    listing_full = client.get("/v1/agents?full=true").json()
    _validator(spec, "AgentList").validate(listing_full)


def test_record_schema_and_token_schema_do_not_drift(spec: dict):
    """token 版本是独立定义（JSON Schema 的 allOf 容不下 additionalProperties: false），
    所以必须有一条断言保证两者不会各走各的。"""
    schemas = spec["components"]["schemas"]
    record = schemas["AgentRecord"]
    with_token = schemas["AgentRecordWithToken"]

    assert set(with_token["properties"]) == set(record["properties"]) | {"manageToken"}
    assert set(record["required"]) <= set(with_token["required"])
    assert with_token["additionalProperties"] is False


def test_error_bodies_conform(service: RegistryService, spec: dict):
    client = TestClient(create_app(service, probe=False))
    for response in (
        client.post("/v1/agents", json={"url": "http://nope.example/card.json"}),
        client.get("/v1/agents/a_0000000000000000"),
        client.delete("/v1/agents/a_0000000000000000"),
    ):
        assert response.status_code in (400, 401, 404, 422, 429)
        _validator(spec, "Error").validate(response.json())


def test_every_operation_id_is_unique(spec: dict):
    identifiers = [
        operation["operationId"]
        for item in spec["paths"].values()
        for method, operation in item.items()
        if method in ("get", "post", "put", "patch", "delete")
    ]
    assert len(identifiers) == len(set(identifiers)) == 5
