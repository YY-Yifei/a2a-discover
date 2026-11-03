"""探测调度。

规范 §6 把探测限定为两个问题：还活着吗、卡变了吗。这里的实现不做任何额外的事 ——
不发 A2A 请求、不建 Task、不测业务延迟、不打分。
"""

from __future__ import annotations

import asyncio

from .service import RegistryService


async def probe_loop(
    service: RegistryService,
    *,
    batch_size: int = 50,
    concurrency: int = 8,
    idle_sleep: float = 5.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """按 ``next_probe_at`` 轮询到期条目并探测，直到 ``stop_event`` 被置位。"""
    stop = stop_event or asyncio.Event()
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(record: dict) -> None:
        async with semaphore:
            # 抓取是阻塞 IO（httpx 同步客户端），放到线程里跑。
            await asyncio.to_thread(service.probe, record)

    while not stop.is_set():
        batch = service.due(batch_size)
        if batch:
            await asyncio.gather(
                *(run_one(record) for record in batch), return_exceptions=True
            )
            continue

        remaining = service.seconds_until_next_probe()
        if remaining is None:
            delay = idle_sleep
        else:
            delay = max(0.5, min(remaining, idle_sleep))
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            continue


def run_probe_once(service: RegistryService, *, limit: int = 50) -> int:
    """同步探测一轮（CLI ``probe --once`` 用），返回探测条数。"""
    batch = service.due(limit)
    for record in batch:
        service.probe(record)
    return len(batch)
