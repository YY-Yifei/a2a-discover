"""受限抓取：把 ``spec/adp-core.md`` §7 的每一条 MUST 落成代码。

一次抓取 = 一次对不可信 URL 的 HTTPS 请求。所有约束在这里集中实施：

* 仅 https、无 userinfo/fragment（``netguard.normalize_url``）；
* 每次请求前重新解析并校验 IP，且连接被钉在这些 IP 上（``netguard.pinned_dns``）；
* 重定向 ≤ 3 跳，**每跳从 URL 重新开始**校验；
* connect ≤ 5s / total ≤ 10s / body ≤ maxCardBytes（流式计数，超限即断）；
* 不转发 Cookie / Authorization / Referer，忽略环境代理（``trust_env=False``）。
"""

from __future__ import annotations

import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from . import netguard

DEFAULT_MAX_BYTES = 1 << 20
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_TOTAL_TIMEOUT = 10.0
DEFAULT_MAX_REDIRECTS = 3
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class FetchError(Exception):
    """抓取失败。``code`` 直接对应规范的错误码。"""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


@dataclass(frozen=True)
class Document:
    """一次成功抓取的结果。``payload`` 是原始 JSON 对象，未做任何加工。"""

    url: str
    status: int
    body: bytes
    payload: dict[str, Any]
    latency_ms: int


class GuardedFetcher:
    """带 SSRF 防护的 HTTPS 抓取器。"""

    def __init__(
        self,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        user_agent: str = "adp-registry/1.0 (+https://github.com/YY-Yifei/a2a-discover)",
    ) -> None:
        self.max_bytes = max_bytes
        self.connect_timeout = connect_timeout
        self.total_timeout = total_timeout
        self.max_redirects = max_redirects
        self.user_agent = user_agent

    # ------------------------------------------------------------------ 对外接口

    def fetch(self, url: str) -> Document:
        try:
            current = netguard.normalize_url(url)
        except netguard.UrlError as exc:
            raise FetchError("BAD_URL", str(exc)) from exc

        started = time.monotonic()
        deadline = started + self.total_timeout

        for _hop in range(self.max_redirects + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("CARD_TIMEOUT", "抓取超过总时长上限")

            host, port = netguard.host_and_port(current)
            try:
                ips = netguard.resolve_public_ips(host, port)
            except netguard.BlockedAddress as exc:
                raise FetchError("CARD_UNREACHABLE", str(exc)) from exc

            status, headers, body = self._request(current, host, port, ips, remaining)

            if status in REDIRECT_STATUSES:
                location = headers.get("location")
                if not location:
                    raise FetchError("CARD_UNREACHABLE", "重定向缺少 Location")
                try:
                    current = netguard.normalize_url(urljoin(current, location))
                except netguard.UrlError as exc:
                    raise FetchError("CARD_UNREACHABLE", f"重定向目标非法：{exc}") from exc
                continue

            if not 200 <= status < 300:
                raise FetchError(
                    "CARD_UNREACHABLE",
                    f"源站返回 HTTP {status}",
                    {"status": status},
                )

            try:
                payload = httpx.Response(200, content=body).json()
            except ValueError as exc:
                raise FetchError("NOT_AN_AGENT_CARD", "响应体不是合法 JSON") from exc
            if not isinstance(payload, dict):
                raise FetchError("NOT_AN_AGENT_CARD", "响应体不是 JSON 对象")

            return Document(
                url=current,
                status=status,
                body=body,
                payload=payload,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        raise FetchError(
            "CARD_UNREACHABLE",
            f"重定向超过 {self.max_redirects} 跳",
            {"maxRedirects": self.max_redirects},
        )

    # ------------------------------------------------------------------ 内部实现

    def _request(
        self,
        url: str,
        host: str,
        port: int,
        ips: list[str],
        remaining: float,
    ) -> tuple[int, dict[str, str], bytes]:
        timeout = httpx.Timeout(
            max(0.1, remaining),
            connect=min(self.connect_timeout, max(0.1, remaining)),
        )
        # 不携带任何调用方凭据；trust_env=False 防止环境代理绕过 IP 固定。
        headers = {
            "user-agent": self.user_agent,
            "accept": "application/json",
        }

        try:
            with netguard.pinned_dns(host, port, ips):
                with httpx.Client(
                    verify=True,
                    follow_redirects=False,
                    timeout=timeout,
                    headers=headers,
                    trust_env=False,
                ) as client:
                    with client.stream("GET", url) as response:
                        status = response.status_code
                        if status in REDIRECT_STATUSES:
                            return status, dict(response.headers), b""
                        body = self._read_capped(response)
                        return status, dict(response.headers), body
        except FetchError:
            raise
        except httpx.TimeoutException as exc:
            raise FetchError("CARD_TIMEOUT", "抓取超时") from exc
        except httpx.ConnectError as exc:
            if _caused_by_ssl(exc):
                raise FetchError("TLS_INVALID", "TLS 证书无效") from exc
            raise FetchError("CARD_UNREACHABLE", f"连接失败：{exc}") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise FetchError("CARD_UNREACHABLE", f"抓取失败：{exc}") from exc

    def _read_capped(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self.max_bytes:
                raise FetchError(
                    "CARD_TOO_LARGE",
                    f"响应体超过 {self.max_bytes} 字节",
                    {"maxCardBytes": self.max_bytes},
                )
            chunks.append(chunk)
        return b"".join(chunks)


def _caused_by_ssl(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False
