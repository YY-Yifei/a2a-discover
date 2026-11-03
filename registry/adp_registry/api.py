"""HTTP 层：严格实现 ``spec/adp-core.yaml`` 的 5 个操作，不多不少。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .card import ID_RE
from .probe import probe_loop
from .service import AdpError, RegistryService, public_record

REGISTRY_CARD_PATH = "/.well-known/a2a-registry.json"
TOKEN_HEADER = "X-ADP-Token"


class Registration(BaseModel):
    """请求体：只有一个字段。"""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


def _error(
    status_code: int, code: str, message: str, detail: dict | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "detail": detail or {}},
    )


def create_app(service: RegistryService, *, probe: bool = True) -> FastAPI:
    """构造 ASGI 应用。``probe=False`` 时不起后台探测（测试与只读部署用）。"""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task | None = None
        if probe:
            task = asyncio.create_task(probe_loop(service))
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(
        title="ADP Core Registry",
        version="1.0.0",
        description=(
            "A2A Discover Protocol — 中心化 Agent Registry。"
            "Registry 只声称：我在 checkedAt 从 url 取到了 card，摘要 cardHash。"
        ),
        docs_url="/docs",
        lifespan=lifespan,
    )

    @app.exception_handler(AdpError)
    async def _adp_error(_request: Request, exc: AdpError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, exc.detail)

    # ---------------------------------------------------------------- 自我描述

    @app.get(REGISTRY_CARD_PATH, tags=["discovery"])
    async def get_registry_card() -> Any:
        return service.registry_card()

    # -------------------------------------------------------------------- 注册

    @app.post("/v1/agents", status_code=201, tags=["agents"])
    async def register_agent(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError:
            return _error(400, "BAD_BODY", "请求体必须是 JSON")
        try:
            registration = Registration.model_validate(payload)
        except Exception as exc:  # pydantic.ValidationError
            return _error(400, "BAD_BODY", "请求体不合法", {"errors": [str(exc)]})

        client_ip = request.client.host if request.client else None
        record, token, created = service.register(registration.url, client_ip=client_ip)

        if not created:
            return JSONResponse(status_code=200, content=public_record(record, full=True))

        body = {**public_record(record, full=True), "manageToken": token}
        return JSONResponse(
            status_code=201,
            content=body,
            headers={"Location": f"/v1/agents/{record['id']}"},
        )

    # -------------------------------------------------------------------- 检索

    @app.get("/v1/agents", tags=["agents"])
    async def list_agents(
        q: str | None = Query(default=None, max_length=200),
        skill: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        changedSince: str | None = None,  # noqa: N803 - 契约里的字段名
        full: bool = False,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Any:
        records, next_cursor = service.list_agents(
            q=q,
            skill=skill,
            tag=tag,
            status=status,
            changed_since=changedSince,
            limit=limit,
            cursor=cursor,
        )
        return {
            "items": [public_record(record, full=full) for record in records],
            "nextCursor": next_cursor,
        }

    # ------------------------------------------------------------------ 单条 / 退订

    @app.get("/v1/agents/{agent_id}", tags=["agents"])
    async def get_agent(agent_id: str) -> Any:
        if not ID_RE.match(agent_id):
            raise AdpError(404, "NOT_FOUND", f"没有 id 为 {agent_id} 的条目")
        return public_record(service.get_agent(agent_id), full=True)

    @app.delete("/v1/agents/{agent_id}", status_code=204, tags=["agents"])
    async def delete_agent(
        agent_id: str,
        token: str | None = Header(default=None, alias=TOKEN_HEADER),
    ) -> JSONResponse:
        if not ID_RE.match(agent_id):
            raise AdpError(404, "NOT_FOUND", f"没有 id 为 {agent_id} 的条目")
        service.delete_agent(agent_id, token)
        return JSONResponse(status_code=204, content=None)

    return app
