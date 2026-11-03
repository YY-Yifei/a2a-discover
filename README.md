# A2A Discover Protocol (ADP)

**A minimal, centralized agent registry for [A2A](https://a2a-protocol.org).**
Registry 只声称一件事，并以此换来极简：*我在 `checkedAt` 从 `url` 取到了 `card`，摘要 `cardHash`。*

[![spec](https://img.shields.io/badge/spec-adp--core%20v1-blue)](spec/adp-core.md)
[![contract](https://img.shields.io/badge/contract-OpenAPI%203.1-green)](spec/adp-core.yaml)

---

## 为什么需要它

A2A 定义了 AgentCard、`/.well-known/agent-card.json`、卡片签名 —— 但**没有**定义 registry API
（见 A2A `docs/topics/agent-discovery.md`：*"The current A2A specification does not prescribe
a standard API for curated registries."*）。

结果是：你知道怎么和一个 Agent 说话，却不知道有谁在那儿。ADP 补的就是这一段，
且只补这一段。

## 一个断言，一套极简

v1 的设计让 Registry **为一个 Agent 背书**（签发 attestation、打健康分、做审核）。
只要它敢背书，就欠下一整套配套：证明（域控挑战）、撤回（墓碑）、更新（漂移不跟随）、
补证（联邦信任）。把这句话抽掉，换成它无法说谎的一句：

> 我在 `checkedAt` 从 `url` 取到了 `card`，摘要 `cardHash`。此外我不声称任何事。

于是下面这些机制**不是被取舍掉的，是自动解体的**：

| 删掉的 | 为什么它自己就没了 |
|---|---|
| 三种域控挑战（DCV） | Registry 自己 fetch，「能注册 ≡ 控制该 URL」，不需要挑战令牌 |
| AgentAttestation + JWKS | A2A 卡片自带 JWS，客户端自己验；Registry 再签 = 多一个没人要求的信任根 |
| 健康分与排序权重 | 权重不可审计，且会腐化成竞价排名 —— 只给原始事实，排序权归 Consumer |
| 协议探测 / SLO 指标 | Registry 是控制面，不发 A2A 请求、看不到业务流量 |
| 8 状态机 | 没有审核流，就只剩 `ok` / `down` |
| 漂移置 `STALE` 不跟随 | 不下断言的人**才敢**跟随：`changedAt` + `prevHash` 两个事实字段 |
| 墓碑 / 抢注保护 | `id = sha256(url)` 且必须抓得到卡，抢注不成立 |
| Feed 事件流 | `?changedSince=` 一个查询参数 |
| 联邦 / 语义检索 / visibility | 首版用不上；私有发现 = 私有实例 |

净效果：**3 path / 5 操作 / 7 schema**，`adp-core.md` 一页读完。

## 五个操作

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/.well-known/a2a-registry.json` | Registry 自我描述（RegistryCard） |
| `POST` | `/v1/agents` | `{url}` → 现场抓取 + 校验 + 入库；`id` 由 URL 派生，重复注册幂等 |
| `GET` | `/v1/agents` | `?q=&skill=&tag=&status=&changedSince=&full=&limit=&cursor=` |
| `GET` | `/v1/agents/{id}` | 单条（含原始 card） |
| `DELETE` | `/v1/agents/{id}` | 退订，带 `X-ADP-Token` |

探测只回答两个问题：**还活着吗**（2xx + 有效 TLS + 合法 AgentCard）、**卡变了吗**
（JCS + sha256 比对 → 自动跟随）。Registry 从不发 A2A 请求、不打分、不排序。

## 三条不变式

1. **Registry 从不下断言** —— 返回的每个字节要么是抓来的，要么是带时间戳的探测事实。
2. **`card` 逐字节原样存** —— A2A 的签名是对 JCS 规范化后的 Card 签的，注入任何字段都会让签名失效。
3. **能被注册 ≡ 控制该 URL** —— 注册就是一次成功的抓取，没有第二条路径。

由 (1)(3) 推出安全结论：**攻破 Registry 只能污染「被发现」，无法伪造「是谁」**。

## 快速开始

```bash
pip install -e "./registry"

# 启动（默认 127.0.0.1:8080，含后台探测）
python -m adp_registry serve --db ./adp.db --port 8080 \
    --public-url https://registry.example.com

# 注册一个 Agent
curl -X POST http://127.0.0.1:8080/v1/agents \
     -H 'content-type: application/json' \
     -d '{"url":"https://geo.example.com/.well-known/agent-card.json"}'

# 检索
curl 'http://127.0.0.1:8080/v1/agents?q=geo&full=true'
curl 'http://127.0.0.1:8080/v1/agents?changedSince=2026-11-01T00:00:00Z'
```

## 仓库结构

```
spec/
  adp-core.md      规范正文（RFC 2119）
  adp-core.yaml    OpenAPI 3.1 契约 —— 机器可读的唯一真源
  validate.py      契约自检（结构 / $ref / 操作集合不得膨胀）
registry/          参考实现：FastAPI + httpx + SQLite
  adp_registry/
    card.py        JCS(RFC 8785) 规范化、cardHash、AgentCard 必填校验
    netguard.py    URL 校验、IP 段拦截、DNS pinning（防 rebinding）
    fetcher.py     受限抓取：≤3 跳重定向、≤1 MiB、≤10s、不转发凭据
    store.py       SQLite：原样存取、固定排序、游标分页、子串检索
    service.py     注册 / 检索 / 退订 / 探测 + 状态机 + 限流
    api.py         5 个操作，不多不少
    probe.py       探测调度
tests/             82 个测试：JCS 向量、SSRF、存储、业务、HTTP、契约一致性
```

## 验证

```bash
python spec/validate.py       # 契约自洽（退出码 0）
python -m pytest -q           # 82 passed
```

`tests/test_spec.py` 会用 `adp-core.yaml` 的 JSON Schema 校验实现真正的响应体 ——
所以**契约与实现漂移，测试先红**。`spec/validate.py` 同时断言操作集合与 `AgentRecord`
的字段集合，谁想加回 `health` / `score` / `trust`，CI 会先拦下他。

## 安全提要

Registry 会代替自己去 `GET` 任意 URL —— 这是本协议唯一的攻击面，规范里逐条列为 MUST：
仅 https、**对解析后的 IP 校验并 pin 住同一个 IP 连接**（否则 DNS rebinding 可绕过）、
重定向每跳重新校验、流式读取即限体积、不转发任何凭据、忽略环境代理。
详见 [`spec/adp-core.md` §7](spec/adp-core.md)。

## 与 A2A 的关系

- 不改 AgentCard 一个字段，复用 `/.well-known/agent-card.json` 与签名机制；
- 不改 A2A 请求语义，Registry 从不发 A2A 请求；
- **可退化**：删掉 Registry，Consumer 仍可直接拉卡、验签、直连。ADP 只把「找到谁」
  从「人工知道域名」变成「一次查询」。

## License

Apache-2.0
