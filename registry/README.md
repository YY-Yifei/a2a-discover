# adp-registry

ADP Core 的参考实现：一个会自己去看的中心化 Agent Registry。

```bash
pip install -e .
python -m adp_registry serve --db ./adp.db --port 8080 --public-url https://registry.example.com
```

| 命令 | 说明 |
|---|---|
| `python -m adp_registry serve` | 启动 HTTP 服务 + 后台探测循环（`--no-probe` 关闭探测） |
| `python -m adp_registry probe` | 只跑探测调度（`--once` 跑一轮就退出） |

行为语义以 [`../spec/adp-core.md`](../spec/adp-core.md) 为准，接口形状以
[`../spec/adp-core.yaml`](../spec/adp-core.yaml) 为准。实现只做这两份文件要求的事：

- 注册即抓取（不变式 I3）：`POST /v1/agents` 同步完成抓取、校验、入库 ——
  这只证明该 URL 此刻放着这张卡，**不证明注册者拥有它**（不抗首次抢注，删除后可被重新注册）；
- Card 的字段与字段值原样保存（I2）：不增删改字段，只在计算 `cardHash` / summary 时读它；
  `cardHash` 是语义摘要，不要求逐字节复现源站报文；
- 探测只答两问：还活着吗、卡变了吗；
- 不下断言（I1）：没有健康分、没有推荐、没有排序。

不能关掉的安全项：`netguard` 的 IP 段拦截与 DNS pinning、`fetcher` 的重定向/体积/时长上限。
它们对应规范的 MUST，不是可配置项。

限流状态放在进程内存里（单实例语义）。多实例部署时把 `_enforce_ip_rate` 换成共享计数器即可，
协议语义不变。
