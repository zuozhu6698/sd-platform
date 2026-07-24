# 06 中间件与部署设计

## 1. APP-SRV 服务

| 服务 | 网络 | 端口暴露 | 数据 |
|---|---|---|---|
| nginx | edge | host 80/443 | 无状态 |
| teable | edge+core+teable-cache | 不直接暴露 | PostgreSQL + redis-teable |
| sd-api | edge+core+app-cache | 不直接暴露 | sd_app/Teable/redis-app |
| sd-worker | core+app-cache | 不暴露 | sd_app/Teable/redis-app |
| postgres | core | 5432 仅绑定 APP LAN IP，防火墙限观远来源 | pg_data |
| redis-teable | teable-cache | 不暴露 | 独立密码与 volume |
| redis-app | app-cache | 不暴露 | 独立密码与 volume，正确性不依赖其持久化 |

Nginx 仅加入 edge；PostgreSQL 在 core，两个 Redis 分别位于 teable-cache/app-cache。数据库和 Redis 不绑定 `0.0.0.0`。

## 2. 网络矩阵

| 来源 → 目标 | 端口 | 必要控制 |
|---|---:|---|
| OA/Browser → Nginx | 443 | 内网 DNS、企业 CA、TLS1.2+、限流 |
| Nginx → sd-api/Teable | 8000/3000 | Compose edge network、Teable 独立内部域名 |
| sd-api/worker → PostgreSQL | 5432 | core、独立角色/密码 |
| sd-api/worker → Redis | 6379 | app-cache、独立密码 |
| sd-api/worker → Teable | 3000 | core、service token |
| worker → OA | 443（或厂商约定） | OA IP allowlist、URL 日志脱敏 |
| worker → GPU-SRV | 8001/443 | 仅 APP-SRV 来源、服务认证 |
| 观远 → PostgreSQL | 5432 | host firewall 限源、bi_reader、仅 bi schema |

## 3. PostgreSQL bootstrap 与迁移

- 首次空卷 bootstrap 只创建角色、`sd_app`、`bi` schema 和最小权限。
- Teable 自己管理其表结构。
- Alembic 在部署 preflight 中升级 `sd_app/bi`，成功后才切换 API/worker。
- 禁止把持续迁移文件简单挂到 `/docker-entrypoint-initdb.d` 并声称已升级。

## 4. Outbox 与任务领取

```sql
SELECT * FROM sd_app.outbox_message
WHERE state = 'pending' AND available_at <= now()
ORDER BY available_at
FOR UPDATE SKIP LOCKED
LIMIT :batch;
```

领取、状态变化和 attempt 记录在一个 PG 事务内。worker 崩溃后由 lease 超时回收。dead letter 只能经带审计的管理接口重放。

Outbox 消费由 `OUTBOX_ENABLED` 独立控制，与 `CRON_ENABLED` 分离。worker 只路由显式注册的 `kind`；未知类型不可重试并立即进入 dead letter，防止配置漂移造成无限重试。OA handler 和真实联调完成前必须保持 outbox 消费关闭。

Wave 1 离线模式的受控 kind 为 `oa.complete_pending` 和 `oa.send_urge`。催办先以规则版本、事项、目标人、业务日期和级别形成稳定 dedup key，在 PG 唯一入队；OA 返回 receipt 后再以相同 dedup key 幂等写 `urge_log`。若 OA 已接收但 Teable 回执写失败，重试必须先由 OA 同键收敛，再补写回执，不得换键重发。

调度阈值配置：`URGE_DUE_SOON_WORKDAYS=5`、`URGE_ESCALATE_AFTER_WORKDAYS=3`、`RECONCILIATION_STALE_MINUTES=5`、`RECONCILIATION_BATCH_SIZE=100`、`URGE_RULE_VERSION=v1`。这是 Gate 3 离线固定数据集基线，正式启用前须关闭 OPEN-11。

人工补发采用双人分权：督导管理员创建审批，异人的运维管理员执行。PostgreSQL 部分唯一索引保证每条 dead letter 同时只有一个有效审批；审批消费、消息重置和两条审计事件分别在对应事务内原子提交。管理列表不返回 payload 或 dedup_key，避免运维角色读取业务正文。

## 5. Redis

- `redis-teable` 只加入 teable-cache；`redis-app` 只加入 app-cache，网络和密码均隔离。
- 应用键必须统一前缀 `sd:{env}:`，登记如下：

| 键 | TTL | 用途 | 权威来源 |
|---|---:|---|---|
| `authz:{person_id}:{version}` | 5min | 角色/范围缓存 | Teable role_assignment |
| `session:{sid}` | ≤JWT exp | session 热缓存 | sd_app.auth_session |
| `csrf:{sid}` | ≤session | CSRF token | sd_app/session 派生 |
| `bff:{query_hash}` | 5min | 只读降级缓存 | Teable/bi |
| `oa:token` | 厂商 TTL-3min | token 热缓存 | OA，可重新获取 |

幂等、outbox、webhook receipt、job_run 不只存 Redis。

计划任务仅由 `sd-worker` 启动。APScheduler 的 `max_instances=1` 和 coalesce 只减少本进程竞争，跨进程正确性仍由 PostgreSQL advisory lock 与 `job_run(job, scheduled_for)` 唯一键保证。所有计划槽使用 `Asia/Shanghai` 并持久化 `scheduled_for`；超过 15 分钟 misfire 窗口不伪造新批次。

## 6. Nginx 要求

- 80 只做 308 跳 HTTPS；443 使用企业 CA 证书。
- 应用域名的 `/api/` 代理 sd-api，根路径托管 web dist；Teable 使用独立内部域名，仅督导办/信息部网络与账号可访问。
- 设置 CSP、HSTS（确认全域 HTTPS 后）、`X-Content-Type-Options`、`Referrer-Policy`、frame-ancestors 精确 OA 域。
- `client_max_body_size 25m`；API 层再做 20MB 单文件限制。
- access log 不记录 query string 中的 ticket/token；OA adapter 不经过 Nginx access log。
- `/healthz` 仅内网监控；`/readyz` 不暴露 secret/版本明细。

## 7. vLLM/GPU-SRV

0.22.1 是截至审查日的 POC 候选，不是未经实机验证的生产承诺；最终版本必须以 P2 的显卡、驱动、模型兼容性和实机帮助输出为准。基线示意：

```bash
vllm serve <approved-model-path> \
  --tensor-parallel-size 2 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --port 8001
```

结构化输出由请求的 `response_format=json_schema`/`structured_outputs` 指定。vLLM 0.12+ 已移除旧 guided API，禁止复制旧 `--guided-decoding-backend outlines`。

## 8. 镜像与制品

- CI 构建 `sd-agent` 和 `web`，生成 SBOM、漏洞扫描报告、commit SHA 标签和 digest。
- 有网环境只负责同步上游镜像到 Harbor；生产服务器不直接拉 Docker Hub/GHCR/npm/PyPI。
- 部署清单保存每个镜像 digest、Git commit、迁移版本、配置 hash、部署人和时间。
- `docker-compose.yml` 中镜像通过必填变量注入；未填不可变镜像时 preflight 必须失败。

## 9. 备份、监控和日志

- PG：WAL 连续归档 + 每日全量 + 至少一份离机加密副本；季度恢复演练。
- 目标：RPO≤15 分钟，RTO≤4 小时。仅有每日 pg_dump 不能宣称达到该目标。
- 日志：容器 json-file `50m × 5`；审计/outbox/job 进数据库；应用日志不保存正文/凭据。
- 指标：请求率/错误率/P95、连接池、outbox backlog/age、job 延迟、OA 成功率、LLM latency/schema failure、备份年龄、磁盘余量。
- 告警：连续 5 分钟 API 5xx>2%、outbox oldest>15min、dead letter>0、job missed、备份失败、磁盘>80%、LLM 连续不可用>30min。

## 10. Compose 状态

根 `docker-compose.yml`、API/worker Dockerfile、web/Nginx 镜像和显式 Alembic runner 已创建，并通过 YAML 解析与本地应用构建；本机没有 Docker，因此 `docker compose config`、镜像构建和 TEST 实机启动仍是 O1 必做门禁。在这些证据完成前状态为 `BUILD_READY_NOT_DEPLOYABLE`，不得执行生产部署。
