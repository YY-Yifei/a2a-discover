"""ADP Core 参考实现 —— 一个会自己去看的中心化 Agent Registry。

Registry 只声称一件事：我在 ``checkedAt`` 从 ``url`` 取到了 ``card``，摘要 ``cardHash``。
此外它什么都不声称：不签名、不评分、不审核、不代理 A2A 流量、不修改 AgentCard。
"""

from __future__ import annotations

from .api import create_app
from .fetcher import Document, FetchError, GuardedFetcher
from .service import AdpError, Config, RegistryService, public_record
from .store import Store

__version__ = "1.0.0"

__all__ = [
    "AdpError",
    "Config",
    "Document",
    "FetchError",
    "GuardedFetcher",
    "RegistryService",
    "Store",
    "__version__",
    "create_app",
    "public_record",
]
