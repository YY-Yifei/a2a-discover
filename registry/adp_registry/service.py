"""Registry 的业务语义：注册、检索、退订、探测。

三条不变式在这里被守住：

* I1 只写事实 —— 记录里没有任何评价性字段；
* I2 Card 的字段与字段值原样存 —— 只在计算 ``cardHash`` 与 summary 时**读**它；
* I3 注册 ≠ 控制该 URL —— ``register()`` 里没有「先登记后验证」的分支，也不声称所有权。
"""

from __future__ import annotations

import hashlib
import random
import secrets
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import card as cardlib
from . import netguard
from .fetcher import FetchError, GuardedFetcher
from .store import Store


class AdpError(Exception):
    """带状态码与机器可读错误码的业务异常（api 层直接映射为响应）。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail or {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(moment: datetime) -> str:
    """统一的 UTC 秒级 ISO-8601（带 Z）—— 定宽，可直接做字符串比较。"""
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_timestamp(value: str) -> str:
    """把外部时间戳规范成与 ``isoformat`` 同一种形状；失败抛 ValueError。"""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return isoformat(moment)


@dataclass(frozen=True)
class Config:
    """Registry 的可调参数。默认值与 ``spec/adp-core.md`` 一致。"""

    registry_name: str = "adp-registry"
    public_url: str = "https://registry.example.com"
    spec_url: str = (
        "https://github.com/YY-Yifei/a2a-discover/blob/main/spec/adp-core.md"
    )
    contact: str = ""
    probe_interval_seconds: int = 900
    jitter_ratio: float = 0.2
    failure_threshold: int = 3
    backoff_base_seconds: int = 60
    backoff_max_seconds: int = 3600
    max_card_bytes: int = 1 << 20
    max_agents_per_domain: int = 100
    registrations_per_ip_per_hour: int = 20


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RegistryService:
    """全部业务行为的唯一入口（api 与 probe 都只调它）。"""

    def __init__(
        self,
        store: Store,
        fetcher: GuardedFetcher | None = None,
        config: Config | None = None,
        *,
        clock: Callable[[], datetime] = utcnow,
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.config = config or Config()
        self.fetcher = fetcher or GuardedFetcher(max_bytes=self.config.max_card_bytes)
        self._clock = clock
        self._rng = rng or random.Random()
        self._register_log: dict[str, list[datetime]] = defaultdict(list)

    # ------------------------------------------------------------------ 时间

    def now(self) -> datetime:
        return self._clock()

    def now_iso(self) -> str:
        return isoformat(self.now())

    # ------------------------------------------------------------------ 注册

    def register(
        self, url: str, *, client_ip: str | None = None
    ) -> tuple[dict, str | None, bool]:
        """注册一个 URL。返回 ``(record, manageToken|None, created)``。

        ``manageToken`` 只在新建时给出，且此后永不轮换（见 spec §5.2）。
        """
        try:
            normalized = netguard.normalize_url(url)
        except netguard.UrlError as exc:
            raise AdpError(400, "BAD_URL", str(exc)) from exc

        self._enforce_ip_rate(client_ip)

        existing = self.store.get_by_url(normalized)
        if existing is not None:
            return existing, None, False

        host, _port = netguard.host_and_port(normalized)
        if self.store.count_host(host) >= self.config.max_agents_per_domain:
            raise AdpError(
                429,
                "RATE_LIMITED",
                f"该域已达注册上限 {self.config.max_agents_per_domain}",
                {"reason": "domain-limit", "host": host},
            )

        document = self._fetch_card(normalized)
        problems = cardlib.validate_card(document.payload)
        if problems:
            raise AdpError(
                422,
                "NOT_AN_AGENT_CARD",
                "源站返回的内容不是合法 AgentCard",
                {"errors": problems},
            )

        now = self.now()
        token = secrets.token_urlsafe(32)
        record = {
            "id": cardlib.derive_id(normalized),
            "url": normalized,
            "host": host,
            "card": document.payload,
            "cardHash": cardlib.card_hash(document.payload),
            "status": "ok",
            "firstSeenAt": isoformat(now),
            "checkedAt": isoformat(now),
            "lastOkAt": isoformat(now),
            "failures": 0,
            "changedAt": None,
            "prevHash": None,
            "_manageTokenHash": hash_token(token),
            "_nextProbeAt": isoformat(self._next_probe_ok(now)),
            "_searchBlob": cardlib.search_blob(document.payload),
        }
        self.store.insert(record)
        return record, token, True

    # ------------------------------------------------------------------ 检索

    def list_agents(
        self,
        *,
        q: str | None = None,
        skill: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        changed_since: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        if status is not None and status not in ("ok", "down"):
            raise AdpError(400, "BAD_STATUS", "status 只能是 ok 或 down")
        if not 1 <= limit <= 100:
            raise AdpError(400, "BAD_LIMIT", "limit 必须在 1..100 之间")
        normalized_since = None
        if changed_since:
            try:
                normalized_since = parse_timestamp(changed_since)
            except ValueError as exc:
                raise AdpError(
                    400, "BAD_TIMESTAMP", f"changedSince 不是合法时间戳：{changed_since}"
                ) from exc
        try:
            return self.store.query(
                q=q,
                skill=skill,
                tag=tag,
                status=status,
                changed_since=normalized_since,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as exc:
            raise AdpError(400, "BAD_CURSOR", str(exc)) from exc

    def get_agent(self, agent_id: str) -> dict:
        record = self.store.get(agent_id)
        if record is None:
            raise AdpError(404, "NOT_FOUND", f"没有 id 为 {agent_id} 的条目")
        return record

    def delete_agent(self, agent_id: str, token: str | None) -> None:
        record = self.store.get(agent_id)
        if record is None:
            raise AdpError(404, "NOT_FOUND", f"没有 id 为 {agent_id} 的条目")
        if not token or not secrets.compare_digest(
            hash_token(token), record["_manageTokenHash"]
        ):
            raise AdpError(401, "UNAUTHORIZED", "manageToken 缺失或不匹配")
        self.store.delete(agent_id)

    # ------------------------------------------------------------------ 探测

    def probe(self, record: dict) -> dict:
        """按 §6 探测一条记录，写回并返回新记录。"""
        now = self.now()
        try:
            document = self.fetcher.fetch(record["url"])
            problems = cardlib.validate_card(document.payload)
            if problems:
                raise FetchError(
                    "NOT_AN_AGENT_CARD",
                    "源站返回的内容不再是合法 AgentCard",
                    {"errors": problems},
                )
        except FetchError as exc:
            return self._record_failure(record, now, exc)
        return self._record_success(record, now, document.payload)

    def due(self, limit: int = 50) -> list[dict]:
        return self.store.due(self.now_iso(), limit)

    def seconds_until_next_probe(self) -> float | None:
        scheduled = self.store.next_probe_at()
        if scheduled is None:
            return None
        try:
            target = parse_timestamp(scheduled)
        except ValueError:
            return 0.0
        moment = datetime.fromisoformat(target.replace("Z", "+00:00"))
        return max(0.0, (moment - self.now()).total_seconds())

    # ------------------------------------------------------------------ 内部

    def _fetch_card(self, url: str):
        try:
            return self.fetcher.fetch(url)
        except FetchError as exc:
            raise AdpError(422, exc.code, exc.message, exc.detail) from exc

    def _record_success(self, record: dict, now: datetime, card: dict) -> dict:
        new_hash = cardlib.card_hash(card)
        changed = new_hash != record["cardHash"]
        updated = dict(record)
        updated.update(
            {
                "card": card,
                "cardHash": new_hash,
                "status": "ok",
                "checkedAt": isoformat(now),
                "lastOkAt": isoformat(now),
                "failures": 0,
                "_nextProbeAt": isoformat(self._next_probe_ok(now)),
                "_searchBlob": cardlib.search_blob(card),
            }
        )
        if changed:
            # 自动跟随：四个字段必须一起变，不能让 card 与 cardHash 短暂不一致。
            updated["prevHash"] = record["cardHash"]
            updated["changedAt"] = isoformat(now)
        self.store.save(updated)
        return updated

    def _record_failure(self, record: dict, now: datetime, exc: FetchError) -> dict:
        failures = int(record["failures"]) + 1
        updated = dict(record)
        updated.update(
            {
                "failures": failures,
                "status": "down" if failures >= self.config.failure_threshold else record["status"],
                "checkedAt": isoformat(now),
                "_nextProbeAt": isoformat(self._next_probe_failure(now, failures)),
            }
        )
        self.store.save(updated)
        return updated

    def _next_probe_ok(self, now: datetime) -> datetime:
        ratio = 1.0 + self._rng.uniform(
            -self.config.jitter_ratio, self.config.jitter_ratio
        )
        return now + timedelta(seconds=self.config.probe_interval_seconds * ratio)

    def _next_probe_failure(self, now: datetime, failures: int) -> datetime:
        exponent = max(0, failures - self.config.failure_threshold)
        delay = self.config.backoff_base_seconds * (2**exponent)
        return now + timedelta(seconds=min(delay, self.config.backoff_max_seconds))

    def _enforce_ip_rate(self, client_ip: str | None) -> None:
        if not client_ip:
            return
        now = self.now()
        window_start = now - timedelta(hours=1)
        log = [moment for moment in self._register_log[client_ip] if moment > window_start]
        if len(log) >= self.config.registrations_per_ip_per_hour:
            raise AdpError(
                429,
                "RATE_LIMITED",
                f"每 IP 每小时最多注册 {self.config.registrations_per_ip_per_hour} 次",
                {"reason": "ip-rate-limit", "limit": self.config.registrations_per_ip_per_hour},
            )
        log.append(now)
        self._register_log[client_ip] = log

    # ------------------------------------------------------------------ 视图

    def registry_card(self) -> dict:
        return {
            "name": self.config.registry_name,
            "apiBase": self.config.public_url.rstrip("/") + "/v1",
            "spec": self.config.spec_url,
            "probeIntervalSeconds": self.config.probe_interval_seconds,
            "limits": {
                "maxCardBytes": self.config.max_card_bytes,
                "maxAgentsPerDomain": self.config.max_agents_per_domain,
            },
            "contact": self.config.contact,
        }


def public_record(record: dict, *, full: bool) -> dict:
    """AgentRecord / AgentSummary 的对外形状（下划线字段一律不外泄）。"""
    base = {
        "id": record["id"],
        "url": record["url"],
        "status": record["status"],
        "checkedAt": record["checkedAt"],
        "lastOkAt": record["lastOkAt"],
        "failures": record["failures"],
        "cardHash": record["cardHash"],
    }
    if full:
        return {
            "id": record["id"],
            "url": record["url"],
            "card": record["card"],
            "cardHash": record["cardHash"],
            "status": record["status"],
            "firstSeenAt": record["firstSeenAt"],
            "checkedAt": record["checkedAt"],
            "lastOkAt": record["lastOkAt"],
            "failures": record["failures"],
            "changedAt": record.get("changedAt"),
            "prevHash": record.get("prevHash"),
        }
    summary = cardlib.summarize(record["card"])
    return {**base, **summary}
