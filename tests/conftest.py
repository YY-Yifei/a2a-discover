"""共享夹具：让测试不依赖真实网络。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "registry"))

from adp_registry import card as cardlib  # noqa: E402
from adp_registry.fetcher import Document, FetchError  # noqa: E402
from adp_registry.service import Config, RegistryService  # noqa: E402
from adp_registry.store import Store  # noqa: E402

PUBLIC_URL = "https://geo.example.com/.well-known/agent-card.json"
OTHER_URL = "https://weather.example.com/.well-known/agent-card.json"


def make_card(**overrides) -> dict:
    """一张最小但合法的 A2A AgentCard。"""
    card = {
        "name": "Geo Skills",
        "description": "Geocoding and reverse geocoding agent",
        "version": "1.0.0",
        "supportedInterfaces": [
            {
                "url": "https://geo.example.com/a2a/v1",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "geocode",
                "name": "Geocode",
                "description": "Convert an address into coordinates",
                "tags": ["geo", "maps"],
            },
            {
                "id": "reverse",
                "name": "Reverse geocode",
                "description": "Convert coordinates into an address",
                "tags": ["geo"],
            },
        ],
    }
    card.update(overrides)
    return card


class FakeFetcher:
    """可编程的假抓取器：按 URL 返回 Document 或抛 FetchError。"""

    def __init__(self) -> None:
        self.docs: dict[str, Document] = {}
        self.errors: dict[str, FetchError] = {}
        self.calls: list[str] = []

    def serve(self, url: str, card: dict) -> None:
        self.docs[url] = Document(
            url=url,
            status=200,
            body=b"",
            payload=card,
            latency_ms=1,
        )
        self.errors.pop(url, None)

    def fail(self, url: str, code: str = "CARD_UNREACHABLE", message: str = "boom") -> None:
        self.errors[url] = FetchError(code, message)

    def fetch(self, url: str) -> Document:
        self.calls.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url in self.docs:
            return self.docs[url]
        raise FetchError("CARD_UNREACHABLE", f"未配置的 URL：{url}")


class FakeClock:
    def __init__(self) -> None:
        from datetime import datetime, timedelta, timezone

        self._moment = datetime(2026, 11, 1, 8, 0, 0, tzinfo=timezone.utc)
        self._step = timedelta(seconds=1)

    def now(self):
        self._moment += self._step
        return self._moment


@pytest.fixture()
def fetcher() -> FakeFetcher:
    fake = FakeFetcher()
    fake.serve(PUBLIC_URL, make_card())
    return fake


@pytest.fixture()
def store() -> Store:
    store = Store(":memory:")
    yield store
    store.close()


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def service(store: Store, fetcher: FakeFetcher, clock: FakeClock) -> RegistryService:
    return RegistryService(store, fetcher, Config(), clock=clock.now)


@pytest.fixture()
def card_hash() -> str:
    return cardlib.card_hash(make_card())
