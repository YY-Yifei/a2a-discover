# A2A Discover Protocol (ADP)

**A minimal, centralized agent registry for [A2A](https://a2a-protocol.org).**

[![status](https://img.shields.io/badge/status-draft%20%C2%B7%20core%20v1-blue)](spec/adp-core.md)
[![spec](https://img.shields.io/badge/spec-ADP%20Core%201.0.0-informational)](spec/adp-core.md)
[![contract](https://img.shields.io/badge/contract-OpenAPI%203.1-green)](spec/adp-core.yaml)
[![license](https://img.shields.io/badge/license-Apache--2.0-lightgrey)](LICENSE)

---

## 它解决什么问题

A2A 定义了 Agent 之间怎么说话：AgentCard、`/.well-known/agent-card.json`、卡片签名。
它没有定义「去哪儿找 Agent」。A2A 文档自己写得很直白：

> The current A2A specification does not prescribe a standard API for curated registries.

结果是：你知道怎么和一个 Agent 说话，却不知道有谁在那儿。ADP 补的就是这一段，且只补这一段。

## Registry 声称什么

只有一句：

> 我在 `lastOkAt` 从 `url` 取到了 `card`，摘要为 `cardHash`。此外不声称任何事。

这句话停在它能证明的地方：不签名、不评分、不审核、不代理 A2A 流量、不修改 AgentCard。

两个时间字段的区分是这句话成立的前提：

| 字段 | 含义 |
|---|---|
| `lastOkAt` | 最近一次**成功**抓取的时刻 |
| `checkedAt` | 最近一次**尝试**的时刻 —— 失败也前进 |

抓取失败时，`card` 仍是上一次成功取到的旧值。所以以 `checkedAt` 为时间点，
「取到了 card」在任意一次失败探测之后就不成立；能支撑这句话的只有 `lastOkAt`。

「注册成功」也只等于「该 URL 此刻放着这张卡」：它不说明注册者拥有该 URL，
也不说明注册者是卡片的主人。详见 [不变式 I3](spec/adp-core.md)。

## API

3 个路径、5 个操作。契约见 [`spec/adp-core.yaml`](spec/adp-core.yaml)（机器可读的唯一真源），
语义见 [`spec/adp-core.md`](spec/adp-core.md)。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/.well-known/a2a-registry.json` | Registry 自我描述（RegistryCard） |
| `POST` | `/v1/agents` | `{"url": …}` → 现场抓取、校验、入库；重复注册幂等 |
| `GET` | `/v1/agents` | `?q=&skill=&tag=&status=&changedSince=&full=&limit=&cursor=` |
| `GET` | `/v1/agents/{id}` | 单条，含 card |
| `DELETE` | `/v1/agents/{id}` | 退订，带 `X-ADP-Token` |

`url` 归一化后充当去重键（scheme 与 host 小写、去掉默认端口 `:443`、百分号编码大写），
同一 URL 的不同书写形式收敛为同一条目。`id` 是 Registry 生成的不透明句柄（URL-safe，
可直接放进 path），**生成算法由实现自选** —— 参考实现用 `a_` + `sha256(归一化 URL)` 前
16 位，换成 UUID、ULID 或自增编号同样合规。Consumer 不得从 `id` 推导 URL。

探测只回答两个问题：**还活着吗**（2xx + 有效 TLS + 合法 AgentCard）、**卡变了吗**
（JCS 规范化后 sha256 比对，变了就跟随）。Registry 从不发 A2A 请求、不打分、不排序。

## 快速开始

要求：Python 3.10+，能向外发起 HTTPS 连接。

```bash
pip install -e "./registry"

# 启动（默认 127.0.0.1:8080，含后台探测循环）
python -m adp_registry serve --db ./adp.db --port 8080 \
    --public-url https://registry.example.com

# 注册一个 Agent
curl -X POST http://127.0.0.1:8080/v1/agents \
     -H 'content-type: application/json' \
     -d '{"url":"https://geo.example.com/.well-known/agent-card.json"}'

# 检索
curl 'http://127.0.0.1:8080/v1/agents?q=geo&full=true'

# 查询某个时间点之后发生内容变化的已有条目（参数为 RFC 3339 时间戳）
curl "http://127.0.0.1:8080/v1/agents?changedSince=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

`?changedSince=` 是可重复读的状态快照查询，不是事件流：它只回答**已有条目**的内容变化，
新注册的条目（`changedAt` 为 `null`）查不到，删除不产生事件，`prevHash` 只保留一跳。

## 它不做什么

ADP 的极简不是省字段，而是**只保留一个能证明的断言，让依附在其它断言上的机制无处可依**：

| 不提供 | 原因 |
|---|---|
| 域控验证（DCV） | Registry 自己 fetch，只证明该 URL 此刻放着这张卡；不证明所有权，就没有需要证明的东西 |
| AgentAttestation / JWKS | AgentCard 自带 JWS，Consumer 自己验；Registry 再签只是多一个没人要求的信任根 |
| 健康分、排序权重、推荐 | 权重不可审计，且会腐化成竞价排名；Registry 只给原始事实，排序权归 Consumer |
| A2A 协议探测 / SLO 指标 | Registry 是控制面，不发 A2A 请求，看不到业务流量 |
| 审核流与多状态机 | 没有审核流程，状态只剩 `ok` / `down` |
| 版本历史 / 事件流 | 内容漂移用 `changedAt` + `prevHash` 两个事实字段跟随，不做逐版存档 |
| 联邦、语义检索、可见性控制 | 首版用不上；需要私有发现，就部署私有实例 |

## 已知代价

目录不声称所有权，有两条直接后果。它们是设计选择，不是缺陷：

- 第一个注册某个 URL 的人持有该条目的 `manageToken`，且 v1 不轮换 ——
  因此**真正的发布者可能始终拿不到自己 URL 的管理权**；
- 条目被 `DELETE` 之后，同一 URL 可被任何人重新注册，并获得新的 token。

需要所有权保证的部署，应依赖 A2A 卡片签名与源站本身，而不是目录。

## 四条不变式

1. **Registry 从不下断言** —— 返回的每个字节要么是抓来的，要么是带时间戳的探测事实。
2. **`card` 的字段与字段值原样存** —— 不增删改字段、不补默认值。`cardHash` 是
   `sha256(JCS(card))` 的**语义**摘要，不要求逐字节复现源站报文。
3. **注册 ≠ 控制该 URL** —— 注册就是一次成功的抓取，只证明「该 URL 此刻放着这张卡」，
   不证明是谁注册的，也不证明注册者是卡片主人。v1 明确不做域控验证（DCV）。
4. **信任由 Consumer 核验，不由 Registry 授予** —— `cardHash` 只保证完整性，身份只来自 A2A 签名。

完整定义见 [`spec/adp-core.md` §3](spec/adp-core.md)。

## 安全模型

**出网抓取。** Registry 会代替自己 `GET` 任意 URL —— 这是本协议主要的攻击面，规范中逐条列为 MUST：
仅 https；对解析后的 IP 校验并 pin 住同一个 IP 连接（否则 DNS rebinding 可绕过）；重定向每跳重新校验；
流式读取即限体积；不转发任何凭据；并且**出网路径不得重新解析目标主机名** ——
环境变量代理、sidecar、mesh 都在此列：https 走代理是 `CONNECT`，DNS 由代理解析，
「校验即连接」这个前提被整体旁路。详见 [`spec/adp-core.md` §7](spec/adp-core.md)。

**目录与所有权。** 任何人都能替一个 URL 注册，先到先得；管理权与代价见上文「已知代价」。

**身份保证的边界。** Registry 手里没有私钥，伪造不出签名。但伪造身份不需要伪造签名：
把一张由攻击者**真实签名**的卡塞进同一批检索结果、排在真卡之前，Consumer 验签全过、
`cardHash` 全对，却选错了人。这条路不篡改任何字节，所以不变式 4 也拦不住。

因此「无法把『是谁』伪造到攻击者名下」是**条件式**的，三个条件 MUST 同时成立：

1. Card 带有效签名；
2. Consumer 有**先验**的公钥锚点，且该锚点绑定的正是它预期的那个身份；
3. Consumer 用该锚点决定了**该看哪一条结果**，而不是先从返回的候选里挑一条、再对挑中的那条验签。

其余情况 —— 无签名、无锚点、TOFU、按检索结果挑选 —— **本协议不提供身份保证**。
签名把防线从 Registry 移到 Consumer 的信任策略上，而不是消除它。

## 与 A2A 的关系

- 不改 AgentCard 一个字段，复用 `/.well-known/agent-card.json` 与既有签名机制；
- 不改 A2A 请求语义，Registry 从不发 A2A 请求；
- **可退化**：删掉 Registry，Consumer 仍可直接拉卡、验签、直连。ADP 只把「找到谁」
  从「人工知道域名」变成「一次查询」。

## 仓库结构

```
spec/
  adp-core.md      规范正文（RFC 2119）
  adp-core.yaml    OpenAPI 3.1 契约 —— 机器可读的唯一真源
  validate.py      契约自检：结构、$ref、操作集合不得膨胀
registry/          参考实现：FastAPI + httpx + SQLite
tests/             契约校验；JCS 向量、SSRF、存储、业务、HTTP 行为；文本层与发布面一致性
```

## 开发

```bash
pip install -e "./registry[dev]"

python spec/validate.py       # 契约自洽，退出码 0
python -m pytest -q           # 全部测试
```

`tests/test_spec.py` 用 `adp-core.yaml` 的 JSON Schema 校验实现返回的真实响应体，
因此契约与实现漂移时测试先失败。改接口必须同步三处：`spec/adp-core.yaml`（契约）、
`spec/adp-core.md`（正文）、`tests/`。

## 状态

当前为 **Draft**（`1.0.0-core`）：契约与参考实现可用，接口在 1.0 定稿前仍可能调整。

参考实现是**单实例**语义（限流状态在进程内存中）；多实例部署需要换成共享计数器，
协议语义不变。

## 参与

接口设计通过 issue 讨论。改动契约前请先开 issue —— 接口形状是本项目的核心决策，
不只是实现细节。

## License

[Apache-2.0](LICENSE)
