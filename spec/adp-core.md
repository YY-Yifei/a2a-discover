# A2A Discover Protocol (ADP) — Core v1

**状态**：Draft
**版本**：1.0.0-core
**契约**：`adp-core.yaml`（OpenAPI 3.1，本文件的机器可读真源）
**自检**：`python spec/validate.py` → 退出码 0

本文使用 RFC 2119 的关键词：MUST / MUST NOT / SHOULD / SHOULD NOT / MAY。

---

## 摘要

ADP 定义一套**中心化**的 Agent 发现接口：一个 Agent Registry 缓存 A2A AgentCard，
回答两个问题 —— **谁在那儿**、**他变了没有**。

Registry 只声称一件事，并且只声称到这个程度：

> 我在 `checkedAt` 从 `url` 取到了 `card`，摘要为 `cardHash`。此外我不声称任何事。

它不签名、不评分、不审核、不代理 A2A 流量、不修改 AgentCard。
因此**攻破 Registry 最多污染「被发现」，无法伪造「是谁」**：冒充一个 Agent
需要同时控制它的源站 URL 与签名私钥，而这两者都不在 Registry 手里。

A2A 本身不定义 registry API（见 A2A `docs/topics/agent-discovery.md`），
但已定义 AgentCard、`/.well-known/agent-card.json` 与卡片签名。ADP 只补目录这一段，
不改 A2A 的任何字段或请求语义。

---

## 1. 一致性

一个实现被称为 **ADP Core 兼容**，当且仅当：

1. 它实现了 `adp-core.yaml` 中的全部 5 个操作，且不额外要求调用方实现任何扩展；
2. 它满足第 3 节的四条不变式；
3. 它满足第 7 节的抓取安全要求（SSRF）；
4. 它对外暴露 `/.well-known/a2a-registry.json` 的 RegistryCard。

Registry MAY 提供额外端点（运营后台、指标），但 MUST NOT 让 Core 的 5 个操作的行为
依赖于这些端点。

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| Registry | 本协议的实现方，缓存 AgentCard 的中心节点 |
| Card | 一个 A2A AgentCard 对象 |
| Consumer | 通过 Registry 发现 Agent 的客户端 |
| Publisher | 把一个 Card 的 `url` 注册进 Registry 的一方 |
| 探测（probe） | Registry 主动 `GET` 已注册 URL 并比对内容的周期性动作 |
| JCS | JSON Canonicalization Scheme，RFC 8785 —— A2A 签名所用的同一套规范化 |

---

## 3. 不变式

这四条是规范的核心，任何实现细节与之冲突时以本节为准。

**I1 — Registry 从不下断言。**
返回的每个字节要么是从源站抓来的（`card`），要么是带时间戳的探测事实
（`cardHash` / `checkedAt` / `lastOkAt` / `failures` / `firstSeenAt` / `changedAt` / `prevHash`）。
Registry MUST NOT 引入任何表示「可信」「质量」「推荐」「认证」的字段。

**I2 — `card` 逐字节原样保存。**
Registry MUST NOT 重排键、补默认值、裁剪字段或注入任何键。
理由：A2A 签名是对「去掉 `signatures` 后的 Card 做 JCS 规范化」得到的字节串签名，
注入任何字段都会让原本合法的签名失效，从而破坏 I4。

**I3 — 能被注册 ≡ 控制该 URL。**
注册即一次成功的抓取，没有第二条路径。Registry MUST NOT 提供「先登记、后验证」的
两阶段流程，也因此不需要任何质询令牌（challenge token）。

**I4 — 信任由 Consumer 核验，不由 Registry 授予。**
Consumer 在使用一个 Agent 之前 MUST 重算 `sha256(JCS(card))` 并与 `cardHash` 比对；
若 Card 带 `signatures`，Consumer MUST 按 A2A 规范验证签名。
`cardHash` 只是**完整性**校验，不构成身份证明 —— Registry 与源站之间的链路如果被攻破，
两侧可以一致地给出一个假卡。身份只来自 A2A 签名。

---

## 4. 资源模型

### 4.1 标识符

```
id       = "a_" + lower_hex(sha256(url))[0:16]        // 17 字符，正则 ^a_[0-9a-f]{16}$
cardHash = "sha256:" + lower_hex(sha256(JCS(card)))   // 71 字符
```

`id` 是 `url` 的确定性函数：不需要唯一性表，重复注册天然幂等，
且**删除后无法被他人抢注** —— 重新注册必须再次抓取成功。

`url` MUST 是规范化后的绝对 URL：scheme 为 `https`，无 userinfo，无 fragment，
不做任何语义重写（不补尾斜杠、不改大小写、不动 query）。同一张卡通过不同 URL 暴露
即两个不同条目，这是规范期望的行为（换 URL = 换身份）。

### 4.2 AgentRecord

| 字段 | 类型 | 语义 |
|---|---|---|
| `id` | string | 见 4.1 |
| `url` | string | 注册时给定的地址，原样回显 |
| `card` | object | A2A AgentCard 原样，可直接喂给 A2A SDK |
| `cardHash` | string | `sha256(JCS(card))` |
| `status` | `ok` \| `down` | 最近一次探测成功 = `ok`；连续失败 ≥ 3 次 = `down` |
| `firstSeenAt` | date-time | 首次成功入库时刻 |
| `checkedAt` | date-time | 最近一次探测时刻（无论成败） |
| `lastOkAt` | date-time | 最近一次探测成功时刻 |
| `failures` | integer | 连续失败次数（成功后归零） |
| `changedAt` | date-time \| null | Card 内容最近一次变化时刻；从未变化为 `null` |
| `prevHash` | string \| null | 变化前的 `cardHash`，供审计 |

**没有别的字段。** 该对象 MUST NOT 出现 `trust` / `health` / `score` / `visibility` /
`attestation` / `etag` 一类的扩展（I1）。

### 4.3 检索 facet

检索只使用 Card 上已有的字段，不引入新字段：

`card.name`、`card.description`、`skills[].id`、`skills[].tags`、`card.status`（条目自身的 `status`）

---

## 5. 操作

契约以 `adp-core.yaml` 为准，此处说明行为语义。

### 5.1 `GET /.well-known/a2a-registry.json`

返回 **RegistryCard**：`name`、`apiBase`、`spec`、`probeIntervalSeconds`、`limits`
（`maxCardBytes`、`maxAgentsPerDomain`）、`contact`。

Registry MUST 在 `apiBase` 下提供 `/agents` 系列端点。该文档遵循 RFC 8615 的
well-known 约定，与 A2A 的 `agent-card.json` 同一套机制。

### 5.2 `POST /v1/agents`

请求 `{"url": "https://…"}`。Registry MUST 在此请求内**同步**完成抓取、校验、入库。

| 结果 | 状态码 | 说明 |
|---|---|---|
| 新建成功 | `201` + `Location` | 响应体为 AgentRecord 加一次性 `manageToken` |
| 已存在 | `200` | 响应体为已存 AgentRecord；**`manageToken` 不轮换** |
| `url` 非法 | `400` `BAD_URL` | 非 https / 带 userinfo / 超长（> 2048） |
| 抓不到或不是合法 Card | `422` `CARD_UNREACHABLE` / `NOT_AN_AGENT_CARD` / `CARD_TOO_LARGE` / `TLS_INVALID` | 见第 6 节校验清单 |
| 触发限流 | `429` `RATE_LIMITED` | 见 5.6 |

重复注册 MUST NOT 轮换 `manageToken` —— 否则任何人都能凭「再注册一次」劫持他人条目。

`manageToken` 是唯一的凭据机制：一个随机 bearer secret，只在 `201` 出现一次，
Registry 侧 MUST 只存其摘要（如 sha256）。丢失即失去退订能力，语义等同于放弃条目。

### 5.3 `GET /v1/agents`

查询参数：`q`、`skill`、`tag`、`status`、`changedSince`、`full`、`limit`、`cursor`。

- `q`：对 `card.name` / `card.description` / `skills[].name` / `skills[].description`
  做**不区分大小写的子串匹配**。实现 MAY 使用全文索引，但 MUST 保证对中文等
  无空格分词的语言同样能子串命中（等价于 `LIKE '%q%'`）。
- `skill`：精确匹配 `skills[].id` 中任一；`tag`：精确匹配 `skills[].tags` 中任一。
- `changedSince`：只返回 `changedAt >= 该时刻` 的条目 —— 这是本协议唯一的「订阅」机制。
- `full=false`（默认）返回 AgentSummary（id/url/name/description/skills/tags/status/
  `cardHash`/`checkedAt`/`lastOkAt`/`failures`）；`full=true` 返回含 `card` 的完整记录。
  默认轻量的理由是：一次检索拉回 20 张 10 KB 的卡是不可接受的。
- 排序 MUST 固定为 `firstSeenAt` 降序、`id` 升序。**Registry 不做相关性排序**
  —— 排序权归 Consumer（I1）。
- `cursor` 是不透明字符串，由 Registry 生成；Consumer MUST NOT 解析它。

### 5.4 `GET /v1/agents/{id}`

返回完整 AgentRecord（含 `card`）。不存在 → `404 NOT_FOUND`。

### 5.5 `DELETE /v1/agents/{id}`

必须携带 `X-ADP-Token`。匹配 → `204`；缺失或不匹配 → `401 UNAUTHORIZED`；
`id` 不存在 → `404 NOT_FOUND`。

删除是立即生效的硬删除。不保留墓碑：`id` 由 URL 派生且必须抓取成功才能注册，
抢注在此模型下不成立。

### 5.6 限流

Registry MUST 至少对注册实施两类限流：

- 每来源 IP：默认 `20` 次/小时（`RATE_LIMITED`）；
- 每域：已注册条目数 ≤ `limits.maxAgentsPerDomain`（默认 100）。

探测流量不受此限流约束。更严格的准入（allow-list、人工审核）属于**部署策略**，
MAY 实施，但 MUST NOT 改变本协议的操作语义。

---

## 6. 探测

探测只回答两个问题。

### 6.1 还活着吗

`GET url` 成功（2xx）**且**满足以下全部条件 → `ok`：

1. TLS 证书有效（不得跳过校验）；
2. 响应体是合法 JSON 且为对象；
3. 通过 AgentCard 必填校验（见 6.3）。

否则计一次失败。**连续失败 ≥ 3 次** → `status = "down"`；**任一次成功** → `ok` 且
`failures = 0`。`checkedAt` 每次探测都更新，`lastOkAt` 只在成功时更新。

### 6.2 卡变了吗

对每次成功探测，Registry MUST 重新计算 `cardHash`：

- 与已存值相同 → 不更新 `card`、`changedAt`、`prevHash`；
- 不同 → 覆盖 `card` 与 `cardHash`，把旧值写入 `prevHash`，`changedAt = checkedAt`。

**自动跟随。** 不下断言的人才敢跟随 —— 因为 Registry 声称的只是「我此刻取到了这个」，
而不是「这个是被我审核过的」。跟随必须原子地同时更新四个字段，不得让
`card` 与 `cardHash` 短暂不一致。

### 6.3 AgentCard 必填校验

至少校验以下字段存在且类型正确：

| 字段 | 要求 |
|---|---|
| `name` | 非空字符串 |
| `description` | 字符串 |
| `version` | 非空字符串 |
| `supportedInterfaces` | 非空数组；每项含 `url`（字符串）与 `protocolBinding`（字符串）；`protocolVersion` 若存在须为字符串 |
| `capabilities` | 对象 |
| `defaultInputModes` | 非空字符串数组 |
| `defaultOutputModes` | 非空字符串数组 |
| `skills` | 数组；每项含非空 `id`（唯一）与非空 `name`；`tags` 若存在须为字符串数组 |
| `signatures` | 若存在须为数组（内容由 A2A 定义，Registry 不验证） |

失败 → `422 NOT_AN_AGENT_CARD`，`detail` MUST 列出全部校验失败的字段路径。

### 6.4 周期

- `ok` → `probeIntervalSeconds`（默认 900s），带 ±20% 抖动；
- `down` → 指数退避 `60 × 2^(failures-3)` 秒，封顶 3600s；
- 单次探测：`connect ≤ 5s`、`total ≤ 10s`、`body ≤ maxCardBytes`（默认 1 MiB）；
- 并发探测 MUST 有全局上限（参考实现默认 8）。

Registry MUST NOT 在探测中发送任何 A2A 协议请求：不调 `SendMessage`、不建 Task、
不测业务延迟、不打分。

---

## 7. 抓取安全（SSRF）

Registry 会代替自己去 `GET` Publisher 给出的任意 URL —— 这是本协议唯一的攻击面。
以下要求是本规范的 **MUST**，任何一条不满足即不兼容。

1. **仅 `https://`**；拒绝带 userinfo 的 URL；端口即 URL 端口，不做推断。
2. **对解析后的 IP 校验，且连接使用的必须正是被校验过的那个 IP。**
   只校验主机名不够 —— DNS rebinding 可以让「校验时的解析」与「连接时的解析」不同。
   实现 MUST 在解析后固定 IP（DNS pinning），拒绝：
   `0.0.0.0/8`、`10/8`、`100.64/10`、`127/8`、`169.254/16`、`172.16/12`、
   `192.0.0/24`、`192.0.2/24`、`192.168/16`、`198.18/15`、`198.51.100/24`、
   `203.0.113/24`、`224/4`、`240/4`、`255.255.255.255/32`、
   `::/128`、`::1/128`、`::ffff:0:0/96`（IPv4-mapped，按映射后的 v4 判定）、
   `2001:db8::/32`、`fc00::/7`、`fe80::/10`、`ff00::/8`。
   任一解析结果落在上述范围 → 整次抓取失败（不做「取公网那个地址」的降级，
   因为混合解析本身就是攻击信号）。
3. **重定向 ≤ 3 跳，每跳从 URL 解析重新开始**（重新校验 scheme、重新解析 IP）。
4. 连接与总时长有硬上限；响应体按流读取并在超过 `maxCardBytes` 时立即中止。
5. 不转发任何 `Cookie`、`Authorization`、`Referer`；不携带调用方凭据。
6. 证书校验 MUST 开启，且 MUST NOT 提供关闭开关用于生产部署。

第 6.3 节的 Card 必填校验是第二道闸：内网服务通常返回不了合法 AgentCard。

---

## 8. 与 A2A 的关系

- **不改 Card 一个字段**，复用 `/.well-known/agent-card.json` 与 A2A 签名机制。
- **不改 A2A 请求语义**，Registry 从不发 A2A 请求。
- **可退化**：删掉 Registry，Consumer 仍可直接 `GET https://{domain}/.well-known/agent-card.json`
  拿到卡、验签、直连。ADP 只把「找到谁」从「人工知道域名」变成「一次查询」。
- 新增的唯一 well-known 文档是 Registry 的**自我描述**
  （`/.well-known/a2a-registry.json`），与 `agent-card.json` 同一套 RFC 8615 约定。

可选扩展（**不在本契约内**）：`https://{domain}/.well-known/a2a-agents.json`
给出一个域下的多张卡 —— A2A 一个域只放一张卡。它是纯增量约定，
不实现它的域完全不受影响。

---

## 9. 安全考量

| 威胁 | 协议回应 |
|---|---|
| Registry 被攻破后冒充 Agent | 不可能（I4）：身份来自 A2A 签名，Registry 无从伪造 |
| 注册他人域名 | 不可能（I3）：注册即抓取，能注册 ≡ 控制该 URL |
| Card 被 Registry 篡改 | `cardHash` + Consumer 重算（I4）；`prevHash` 提供审计线索 |
| SSRF（内网探测） | 第 7 节 |
| 抢注 / 条目劫持 | `id` 由 URL 派生；`manageToken` 不轮换 |
| 一次检索拉爆带宽 | 默认返回 summary，`full=true` 显式索取 |
| 低质 / 恶意 Agent 泛滥 | 协议不解决（I1）。准入门槛 = 真域名 + 有效 TLS + 合法 Card；更高要求是部署策略 |

Registry 自身 SHOULD 记录探测历史（时间、结果、hash）以便事后审计；
历史记录不进入 AgentRecord，也不对外暴露为「评分」。

---

## 10. 实现

参考实现：`registry/`（FastAPI + httpx + SQLite，5 个操作 + 探测循环）。

```bash
pip install -e "./registry"
python -m adp_registry serve --db ./adp.db --port 8080 --public-url https://registry.example.com
```

契约一致性测试：`python spec/validate.py && python -m pytest`
