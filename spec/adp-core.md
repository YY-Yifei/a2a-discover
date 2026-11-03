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

> 我在 `lastOkAt` 从 `url` 取到了 `card`，摘要为 `cardHash`。此外我不声称任何事。

这个时间点 MUST 是最近一次**成功**抓取的时刻。最近一次**尝试**的时刻记为 `checkedAt`：
它失败也会前进，而失败时 `card` 仍是上一次成功取到的旧值 —— 以尝试时刻作为这句话的时间点，
任意一次失败探测之后这句话就系统性为假。能支撑「取到了」的，只有 `lastOkAt`。

它不签名、不评分、不审核、不代理 A2A 流量、不修改 AgentCard。

**它也不证明任何事。** 注册成功只说明「此刻这个 URL 上放着这张卡」：
既不说明注册者拥有这个 URL（任何人都能替一个 URL 注册），也不说明注册者就是卡片的主人。
所有权是 A2A 签名与源站的事，不是目录的事 —— v1 因此明确**不做**域控验证（DCV，见 I3）。

由此，攻破 Registry 的后果才可界定 —— 但**有上界不等于「无法伪造身份」**。
Registry 手里没有私钥，伪造不出签名；可伪造身份不需要伪造签名：它只要把一张
**由攻击者真实签名**的卡塞进同一批检索结果、排在真卡之前，Consumer 验签全过、
`cardHash` 全对，却选错了人。这条路径不篡改任何字节，所以 I4 也拦不住。

结论是**条件式**的，以下三个条件 MUST 同时成立：

1. Card 带有效签名；
2. Consumer 有**先验**的可信公钥锚点，且该锚点绑定的正是它**预期**的那个身份；
3. Consumer 用该锚点决定了**该看哪一条结果** —— 而不是从 Registry 返回的候选里挑选，
   再对挑中的那条验签。

三者全部成立时，Registry 才无法把「是谁」伪造到攻击者名下。其余任何情况（无签名、
无锚点、TOFU、按检索结果挑选、只验签不锚定）—— **本协议不提供身份保证**，也不得
据此推理任何安全结论。签名把伪造身份的防线从 Registry 移到 Consumer 的信任策略上，
而不消除它：没有先验锚点时，验签只能退化为「首次即信任」（TOFU）。

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
| Registrant（注册者） | 对某个 `url` 发起 `POST /v1/agents` 的一方。**不等于**该 URL 的控制者 |
| 探测（probe） | Registry 主动 `GET` 已注册 URL 并比对内容的周期性动作 |
| JCS | JSON Canonicalization Scheme，RFC 8785 —— A2A 签名所用的同一套规范化 |

---

## 3. 不变式

这四条是规范的核心，任何实现细节与之冲突时以本节为准。

**I1 — Registry 从不下断言。**
返回的每个字节要么是从源站抓来的（`card`），要么是带时间戳的探测事实
（`cardHash` / `checkedAt` / `lastOkAt` / `failures` / `firstSeenAt` / `changedAt` / `prevHash`）。
Registry MUST NOT 引入任何表示「可信」「质量」「推荐」「认证」的字段。

**I2 — `card` 的字段与字段值原样保存。**
Registry MUST NOT 增加、删除或修改 `card` 的字段与字段值：不补默认值、不裁剪字段、
不改变任何值、不注入任何键。**这不要求逐字节保持源站报文**：`card` 以 JSON 对象形式
提供，键的顺序、空白与字符转义由实现与传输层决定，因此 `cardHash` 是**语义**摘要，
不是原始报文摘要；原始报文的逐字节一致性不属于本协议。

理由：A2A 签名是对「去掉 `signatures` 后的 Card 做 JCS（RFC 8785）规范化」得到的，
而 JCS 本身已经规范了键序、数字与字符串转义 —— 所以重排键**不会**破坏签名；
但增删字段、或修改**被签名区域内**的字段值，会改变 JCS 的输入，**会**破坏签名，
从而破坏 I4。准确说法是「改被签名区域才破签名」，不是「改任何字段都破」。

`cardHash` 与签名的**作用域不同**，Consumer MUST NOT 把两者当作等价：

- `cardHash = sha256(JCS(card))` 覆盖**含 `signatures` 的整卡** —— 任何改动，
  包括改 `signatures` 自身，都会让它变化；
- 签名**不覆盖** `signatures`，只对被签名区域敏感。

所以「`cardHash` 吻合」只说明「你拿到的卡与 Registry 声称的那张是同一张」，
**不说明签名有效**；两者都要验，判据不同，能发现的攻击也不同。

若部署方确有「原始报文审计」需求，MUST 用契约之外的独立字段（例如 base64 的 `rawCard`）
承载，MUST NOT 把原始字节塞进 `card`，也 MUST NOT 往 `card` 里加键。

**I3 — 注册 ≠ 控制该 URL。**
注册即一次成功的抓取，没有第二条路径。这保证 `card` 一定是**此刻从该 URL 真实取到**的；
它**不**保证**谁**注册了它，也不保证注册者与卡片主人有任何关系。
Registry MUST NOT 由此声称任何所有权，MUST NOT 提供「先登记、后验证」的两阶段流程，
也 MUST NOT 引入质询令牌（challenge token）来事后补救 —— 所有权属于 A2A 签名与源站，
不属于目录。需要准入控制的部署走部署策略（§5.6），但那不改变本协议的操作语义。

推论（实现、契约与文档 MUST 一致地承认）：

1. 首次注册者获得 `manageToken`，条目已存在时它 MUST NOT 轮换（§5.2）。
   因此**真正的发布者可能永远拿不到自己条目的管理权** —— 这是本协议接受的代价；
   协议不提供找回路径，运维介入不属于协议的一部分。
2. 删除是硬删除且不留墓碑，所以删除后**任何人都可以重新注册并成为新的首注册者**。
   `id` 由 URL 派生只保证「同一 URL 只有一个条目」，不保证「条目归属于最初那个人」。
3. 因此任何实现、README 或接口描述 MUST NOT 声称「能注册 ≡ 控制该 URL」，
   也不得把它当作安全结论的推理前提。

**I4 — 信任由 Consumer 核验，不由 Registry 授予。**
Consumer 在使用一个 Agent 之前 MUST 重算 `sha256(JCS(card))` 并与 `cardHash` 比对；
若 Card 带 `signatures`，Consumer MUST 按 A2A 规范验证签名。
`cardHash` 只是**完整性**校验，不构成身份证明 —— Registry 与源站之间的链路如果被攻破，
两侧可以一致地给出一个假卡。身份只来自 A2A 签名，而「该签名属于谁」由 Consumer 的
信任策略判定；ADP 本身**不提供身份保证**（无先验公钥锚点时，验签退化为 TOFU）。

---

## 4. 资源模型

### 4.1 标识符

```
id       = "a_" + lower_hex(sha256(normalized_url))[0:16]   // 17 字符，正则 ^a_[0-9a-f]{16}$
cardHash = "sha256:" + lower_hex(sha256(JCS(card)))         // 71 字符
```

`id` 是**归一化后** `url` 的确定性函数：不需要唯一性表，重复注册天然幂等。

`url` 的归一化 MUST 且仅 MUST 做以下三件事，其余一律原样保留：

1. scheme 与 host 转小写（`HTTPS://Geo.Example.com/…` → `https://geo.example.com/…`）；
2. 去掉默认端口 `:443`（`https://geo.example.com:443/a` 与 `https://geo.example.com/a` 同一条目）；
3. 百分号编码统一为大写十六进制（`%2f` → `%2F`，path 与 query 各做一次）。

因此 `https://Geo.Example.com:443/Card.json` 与 `https://geo.example.com/Card.json`
是**同一个条目**；而 `…/Card.json` 与 `…/Card.json/` 是**两个**条目。

允许的 URL：绝对 URL、scheme 为 `https`（大小写不敏感，归一为小写）、无 userinfo、
无 fragment、**host 为纯 ASCII**。非 ASCII host MUST 被拒绝（`400 BAD_URL`），
**MUST NOT** 做 IDNA 映射：`https://例子.测试/card` 与
`https://xn--fsqu00a.xn--0zwm56d/card` 是同一主机却会得到两个 `id`；而 UTS-46 的
映射规则是**算法版本相关**的（`ß`、ZWJ 的处理都变过）—— 一旦纳入，`id` 就从
「URL 的确定性函数」退化为「URL + 我们这一版的 IDNA」，直接违背 4.1 的立身之本。
需要非 ASCII 域的注册者自行转写为 punycode（那是 ASCII）。

MUST NOT 做任何**语义**重写：不补尾斜杠、不合并 `/./` 与 `/../`、不排序或改写 query、
不补默认 path。fragment MUST 被拒绝而不是悄悄剥掉 —— `id` 由 URL 派生，
静默改写会让 `id` 漂移。

同一张卡通过两个不同的 path 或两个不同主机名暴露，就是两个不同条目 —— 这是规范期望的
行为（换 URL = 换**条目**，也就换 `id`）。注意这**不是**「换身份」：身份来自 A2A 签名
（I4），与 `id` 无关；`id` 只保证「同一 URL 只有一个条目」，不保证条目归属于谁（I3）。
归一化处理的是**同一 URL 的书写变体**（大小写、默认端口、百分号编码），
不是「同一资源的多个地址」。

### 4.2 AgentRecord

| 字段 | 类型 | 语义 |
|---|---|---|
| `id` | string | 见 4.1 |
| `url` | string | 归一化后的地址（规则见 4.1），与该条目一一对应 |
| `card` | object | A2A AgentCard，字段与字段值原样，可直接喂给 A2A SDK |
| `cardHash` | string | `sha256(JCS(card))`，**语义**摘要 |
| `status` | `ok` \| `down` | `down` ⇔ 连续失败 ≥ 3 次；否则为 `ok`（失败未达 3 次时仍为 `ok`，但 `failures` > 0 且 `checkedAt` 已前进） |
| `firstSeenAt` | date-time | 首次成功入库时刻 |
| `checkedAt` | date-time | 最近一次探测**尝试**时刻（无论成败，失败也会前进）。MUST NOT 用它表述「此刻取到了 card」—— 那是 `lastOkAt` |
| `lastOkAt` | date-time | 最近一次探测成功时刻 —— **核心断言所指的时间点** |
| `failures` | integer | 连续失败次数（成功后归零） |
| `changedAt` | date-time \| null | 内容最近一次变化时刻；从未变化为 `null`。**该键 MUST 始终存在** |
| `prevHash` | string \| null | 变化前的 `cardHash`，供审计；从未变化为 `null`。**该键 MUST 始终存在** |

两个字段 MUST 始终存在（不得省略）：省略与 `null` 是两种不同的行为，同时允许会让
「从未变化」出现两种编码，Consumer 得多写一条分支。契约里两者都在 `required` 里。

**没有别的字段。** 该对象 MUST NOT 出现 `trust` / `health` / `score` / `visibility` /
`attestation` / `etag` 一类的扩展（I1）。

### 4.3 检索 facet

检索只使用 Card 上已有的字段，不引入新字段：

`card.name`、`card.description`、`skills[].id`、`skills[].tags`、条目的 `status`

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
| `url` 非法 | `400` `BAD_URL` | 非 https / 带 userinfo / 带 fragment / 非 ASCII host / 超长（> 2048） |
| 抓不到或不是合法 Card | `422` `CARD_UNREACHABLE` / `NOT_AN_AGENT_CARD` / `CARD_TOO_LARGE` / `TLS_INVALID` | 见第 6 节校验清单 |
| 触发限流 | `429` `RATE_LIMITED` | 见 5.6 |

重复注册 MUST NOT 轮换 `manageToken` —— 否则任何人都能凭「再注册一次」劫持他人条目。

`manageToken` 是**首个注册者**（first registrant）的管理凭据，**不是所有权证明**：
它只回答「谁先来」。首个注册者与该 URL 的真实控制者 MUST NOT 被混同 ——
本协议不验证后者（I3），也没有找回路径，既包括 token 丢失，也包括真正的发布者
从未拿到它（条目已被他人抢先注册）。需要人工干预的情形交给部署方，不属于协议。

`manageToken` 是唯一的凭据机制：一个随机 bearer secret，只在 `201` 出现一次，
Registry 侧 MUST 只存其摘要（如 sha256）。

### 5.3 `GET /v1/agents`

查询参数：`q`、`skill`、`tag`、`status`、`changedSince`、`full`、`limit`、`cursor`。

- `q`：对 `card.name` / `card.description` / `skills[].name` / `skills[].description`
  做**不区分大小写的子串匹配**。实现 MAY 使用全文索引，但 MUST 保证对中文等
  无空格分词的语言同样能子串命中（等价于 `LIKE '%q%'`）。
- `skill`：精确匹配 `skills[].id` 中任一；`tag`：精确匹配 `skills[].tags` 中任一。
- `changedSince`：只返回 `changedAt >= 该时刻` 的条目。它只回答**已有条目**的
  **内容变化**，**不是 feed**（变更流），Consumer MUST NOT 把它当变更流使用：
  - 新注册的条目 `changedAt` 为 `null`，**不会被任何 `changedSince` 查到**；
  - 删除不产生事件（无墓碑，见 §5.5）；
  - `prevHash` 只保留上一版，没有逐版历史。

  比较 MUST 按**时间点**（instant）进行，MUST NOT 按字符串比较 —— 带 `+08:00` 之类
  偏移的合法 `date-time` 与 `Z` 串做文本比较会错序。出参一律是秒级定宽 UTC（见 4.2），
  入参不要求定宽，MAY 带任意合法偏移，由 Registry 解析成时间点后再比。

  这是**可重复读的状态快照查询**，不是投递：边界用 `>=`、出参时间戳为秒级，
  所以边界上的记录会被重复返回（Consumer MUST 幂等去重）。**中间版本会丢失**：
  同一秒内的多次变化只留最后一个 `changedAt`，`v1 → v2 → v3` 里 `v1`、`v2`
  永远不会出现。因此本协议**不提供逐变更投递保证** —— 既不声称 at-least-once，
  更不声称 exactly-once。需要完整变更流的部署 MAY 在 Core 之外另建 feed 或
  单调序列，但那不在本契约内。
- `full=false`（默认）返回 AgentSummary：`id` / `url` / `name` / `description` /
  `skills` / `tags` / `status` / `cardHash` / `checkedAt` / `lastOkAt` / `failures`
  —— 以上**全部是必填**（契约的 `required` 与之一致，不得「可以合法地不给」）。
  `full=true` 返回含 `card` 的完整记录。
  默认轻量的理由是：一次检索拉回 20 张 10 KB 的卡是不可接受的。
- 排序 MUST 固定为 `firstSeenAt` 降序、`id` 升序。**Registry 不做相关性排序**
  —— 排序权归 Consumer（I1）。
- `cursor` 是不透明字符串，由 Registry 生成；Consumer MUST NOT 解析它。

### 5.4 `GET /v1/agents/{id}`

返回完整 AgentRecord（含 `card`）。不存在 → `404 NOT_FOUND`。

### 5.5 `DELETE /v1/agents/{id}`

必须携带 `X-ADP-Token`。匹配 → `204`；缺失或不匹配 → `401 UNAUTHORIZED`；
`id` 不存在 → `404 NOT_FOUND`。

删除是立即生效的硬删除，不保留墓碑。`id` 由 URL 派生、重新注册必须再次抓取成功，
所以被删除的条目**可以被任何人重新注册**，并由新的首注册者拿走新的 `manageToken`。
这是 I3 的直接后果：目录不声称归属，也就没有需要保护的归属。
Consumer MUST NOT 把「条目存在」或「`firstSeenAt` 很早」当作长期归属的证据。

### 5.6 限流

Registry MUST 至少对注册实施两类限流：

- 每来源 IP：默认 `20` 次/小时（`RATE_LIMITED`）；
- 每域：同一**主机名**（`url` 的 host，小写、不含端口与路径）下已注册条目数
  ≤ `limits.maxAgentsPerDomain`（默认 100）。计数按主机名归并、**不区分 path**：
  同一主机名下的多个 path 共享同一预算，所以它**能**限制「用同一域名下的多路径堆量」；
  它**不**限制跨主机名（含子域）的堆量 —— 那个量级属于部署策略（allow-list /
  人工审核），不属于 Core。

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
`failures = 0`。失败 1~2 次期间 `status` **保持** `ok`，但 `failures` > 0 且
`checkedAt` 已前进 —— Consumer SHOULD 结合 `failures` 而不是只看 `status` 判断新鲜度。
`checkedAt` 每次探测都更新（失败也前进），`lastOkAt` 只在成功时更新。
因此**「我在此刻取到了卡」的时间点只能是 `lastOkAt`**，不是 `checkedAt`。

### 6.2 卡变了吗

对每次成功探测，Registry MUST 重新计算 `cardHash`：

- 与已存值相同 → 不更新 `card`、`changedAt`、`prevHash`；
- 不同 → 覆盖 `card` 与 `cardHash`，把旧值写入 `prevHash`，`changedAt = lastOkAt`
  （本次成功探测的时刻，与 `lastOkAt` 同值）。MUST NOT 用 `checkedAt`：失败探测会让
  `checkedAt` 前进，那会把「内容变化时刻」记成一次**失败的**探测时刻。

**自动跟随。** Registry 能跟随内容漂移，正是因为它对内容没有断言：它声称的只是
「我此刻取到了这个」，而不是「这个是被我审核过的」。跟随必须原子地同时更新四个字段，不得让
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

Registry 会代替自己去 `GET` Registrant 给出的任意 URL —— 这是本协议唯一的攻击面。
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
7. **出网路径不得重新解析目标主机名。** 抓取 MUST NOT 经由任何会重新解析目标主机名
   的中间层 —— 包括 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 等环境变量代理、
   sidecar、service mesh、egress gateway。理由不是「代理不可信」，而是它**整体旁路**
   第 2 条：https 经代理是 `CONNECT host:port`，**由代理完成 DNS 解析与建连**，
   于是 Registry 校验的是 A、实际连的是 B —— 「校验即连接」这个前提直接不成立，
   不是打了折扣。若部署确实需要代理，第 2 条与第 6 条的等价要求在代理侧仍 MUST
   成立（例如代理自身做同样的解析后校验与 IP 固定）；**无法证明这一点时视为不兼容**，
   MUST NOT 以「尽力而为」代替。

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
| Registry 被攻破后冒充 Agent | 签名伪造不出来，但**身份可以被嫁接到攻击者名下**：把攻击者**真实签名**的卡排进检索结果前列，Consumer 验签全过却选错人 —— 全程不篡改任何字节。上界是条件式的（摘要）：① 有效签名 ② 先验公钥锚点锚定的正是预期身份 ③ 用锚点决定该看哪一条结果；三者同时成立才成立，其余情况（含 TOFU、按结果挑选）**无身份保证** |
| 注册他人域名 | **可以**（I3）。注册只要求「该 URL 此刻放着一张合法卡」，不需要任何所有权证明 |
| 发布者失去自己条目的管理权 | 已知限制（I3 推论 1）：首注册者拿走 `manageToken` 且不轮换，真正的发布者可能永远拿不到。恢复只有部署方人工介入，不是协议能力 |
| Card 被 Registry 篡改 | **`cardHash` 不抵御篡改者** —— 摘要与被篡改的 `card` 出自同一只手，重算必然自洽。它的作用是**发现不一致**：源站仍在时 Consumer 直接拉源站比 `cardHash`；`prevHash` 只提供审计线索。能抵御篡改的只有 A2A 签名（I4）。注意 `cardHash` 是**语义**摘要（I2），不覆盖键序与空白，但覆盖 `signatures` |
| SSRF（内网探测） | 第 7 节 |
| 抢注 / 条目劫持 | 部分防护：`manageToken` 不轮换挡住「再注册劫持」，**不挡首次抢注**；删除后条目可被任何人重新注册（§5.5） |
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

### 升级提示：`url` 归一化（§4.1）

归一规则生效前写入的条目，其 `url` 与 `id` 是按**未归一**的字符串派生的。升级时实现 MUST：

1. 对每条既有记录重算 `id = derive_id(normalize_url(url))`，并把 `url` 写成归一后的值；
2. 合并重算后落在同一个 `id` 上的重复条目 —— **只有 `normalized_url` 逐字节相等时才可以合并**。
   `id` 只有 64 bit（§4.1），不同 URL 撞上同一 `id` 是可能的；把碰撞当重复会**静默丢掉
   一个真实条目**。实现 MUST 在此处断言：`normalized_url` 不相等却落到同一 `id` ⇒ 报错
   停止迁移，MUST NOT 合并。
3. 合并时每个字段的取法 MUST 明确，不得依赖遍历顺序：`firstSeenAt` 取最早；
   `card` / `cardHash` / `lastOkAt` / `changedAt` / `prevHash` 取 `lastOkAt` 最晚的那条
   （`card` 与 `cardHash` MUST 同进同退，不得跨条目拼接）；`status` / `failures` 取同一条的；
   `manageToken` **取 `firstSeenAt` 最早的那条**（与 I3 推论 1 一致）并留下迁移记录 ——
   否则原注册者的 token 会静默失效，等于迁移脚本把 I3 已承认的「永久失去管理权」再造一遍。
4. 删除原文本身已不合法的条目（非 https / 带 fragment / 非 ASCII host ——
   它们不可能再被抓取）。

这一步不可跳过：跳过会让「同一个 URL 的两种写法」在库里长期并存，而 4.1 的新规则
只能保证**新写入**的条目归一。

