"""命令行入口。

    python -m adp_registry serve --db adp.db --port 8080
    python -m adp_registry probe --db adp.db --once
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .api import create_app
from .fetcher import GuardedFetcher
from .probe import probe_loop, run_probe_once
from .service import Config, RegistryService
from .store import Store


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="adp.db", help="SQLite 文件路径（默认 adp.db）")
    parser.add_argument(
        "--public-url",
        default="https://registry.example.com",
        help="本 Registry 的对外地址，写进 RegistryCard.apiBase",
    )
    parser.add_argument("--name", default="adp-registry", help="RegistryCard.name")
    parser.add_argument(
        "--spec-url",
        default=Config.spec_url,
        help="RegistryCard.spec 指向的规范地址",
    )
    parser.add_argument("--contact", default="", help="运维联系方式")
    parser.add_argument(
        "--probe-interval",
        type=int,
        default=Config.probe_interval_seconds,
        help="健康条目的探测周期（秒）",
    )
    parser.add_argument(
        "--max-card-bytes", type=int, default=Config.max_card_bytes, help="单张卡体积上限"
    )
    parser.add_argument(
        "--max-agents-per-domain",
        type=int,
        default=Config.max_agents_per_domain,
        help="每域条目上限",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adp-registry",
        description="A2A Discover Protocol (ADP) Core — 参考实现",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动 HTTP 服务（含后台探测）")
    _add_common(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument(
        "--no-probe", action="store_true", help="不起后台探测（只读部署 / 调试）"
    )

    probe = sub.add_parser("probe", help="跑探测（--once 只跑一轮到期条目）")
    _add_common(probe)
    probe.add_argument("--once", action="store_true", help="只跑一轮")
    probe.add_argument("--batch-size", type=int, default=50)
    probe.add_argument("--concurrency", type=int, default=8)

    return parser


def build_service(args: argparse.Namespace) -> RegistryService:
    config = Config(
        registry_name=args.name,
        public_url=args.public_url,
        spec_url=args.spec_url,
        contact=args.contact,
        probe_interval_seconds=args.probe_interval,
        max_card_bytes=args.max_card_bytes,
        max_agents_per_domain=args.max_agents_per_domain,
    )
    store = Store(args.db)
    fetcher = GuardedFetcher(max_bytes=config.max_card_bytes)
    return RegistryService(store, fetcher, config)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = build_service(args)

    if args.command == "serve":
        import uvicorn

        app = create_app(service, probe=not args.no_probe)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0

    if args.command == "probe":
        if args.once:
            count = run_probe_once(service, limit=args.batch_size)
            print(f"探测完成：{count} 条到期条目")
            return 0
        try:
            asyncio.run(
                probe_loop(
                    service,
                    batch_size=args.batch_size,
                    concurrency=args.concurrency,
                )
            )
        except KeyboardInterrupt:
            print("已停止")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
