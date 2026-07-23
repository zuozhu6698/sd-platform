# 01 架构与边界

## 1. 设计目标

一期只解决四件事：安全填报、确定性催办、全过程审计、按权限展示。AI 审读与周报成文是增强能力，不能阻塞主链路。

## 2. 部署拓扑

```text
                                   ┌────────────────────────────┐
                                   │ GPU-SRV / 集团模型服务      │
                                   │ vLLM + approved model       │
                                   └──────────────▲─────────────┘
                                                  │ HTTPS/8001 allowlist
┌───────────── APP-SRV（单机 Compose，已确认角色）────────────────────────────┐
│                                                                            │
│  Browser/OA ──TLS──> Nginx ──> web static                                  │
│                         ├────> sd-api (FastAPI, no scheduler)               │
│                         └────> Teable admin UI（仅督导办/信息部）           │
│                                      │                                     │
│                    ┌─────────────────┴──────────────────┐                  │
│                    │                                    │                  │
│                sd-worker                           Teable CE                │
│            scheduler/outbox/AI/OA                  domain data              │
│                    │                                    │                  │
│          redis-app cache                    redis-teable cache              │
│                    └─────────── PostgreSQL ──────────────┘                  │
│                           Teable-owned tables                               │
│                           sd_app schema                                     │
│                           bi schema/views                                   │
└────────────────────────────────────────────────────────────────────────────┘
          │OA REST/SSO                 │PG TLS/read-only
          ▼                            ▼
      致远 OA                        观远 BI
```

APP-SRV 的具体地址、SSH 账户和密码不写入仓库。GPU 端点当前为 `pending`。

## 3. 组件职责

| 组件 | 负责 | 禁止 |
|---|---|---|
| Nginx | TLS、静态文件、反代、限流、安全头、访问日志脱敏 | 业务鉴权、记录 OA/LLM 完整 URL |
| web | PC/H5 交互、状态展示、可恢复错误提示 | 直连 Teable/PG/vLLM、保存凭据、用 UI 显隐代替鉴权 |
| sd-api | SSO、会话、RBAC、BFF、提交命令、查询、审计入口 | 运行 scheduler、同步等待长批任务、直接 SQL 写 Teable 内部表 |
| sd-worker | scheduler、outbox、OA、AI、对账、快照、失败补偿 | 对外提供用户接口、依赖 Redis 保证唯一执行 |
| Teable | 9 张领域表、督导办维护界面、REST/Webhook | 普通用户共享表单、行级权限边界、应用运行日志 |
| PostgreSQL `sd_app` | 会话、幂等、审计、outbox、job、报告版本、AI 溯源 | 保存明文密钥、直接修改 Teable 表 |
| PostgreSQL `bi` | 稳定只读视图/物化快照 | 接受业务写入、暴露 Teable 内部字段名 |
| Redis | 会话/权限/只读缓存与锁加速 | 成为审计、幂等、outbox 的唯一真相 |
| vLLM | 无状态结构化推理 | 业务决策、数据库/工具访问 |

## 4. 正式填报链路

普通用户不访问 Teable 共享表单。PC/H5 均使用自研表单，经 BFF 提交。

```text
OA 深链
  │  ticket + relative redirect
  ▼
SSO callback ──> session cookie + CSRF token
  │
  ▼
GET task ──RBAC──> 返回允许字段 + can.report
  │
  ▼
POST /api/report/submit + Idempotency-Key
  │
  ├─ 校验 session/RBAC/字数/进度/附件扫描/重复提交
  ├─ sd_app.submission_command INSERT（唯一键）
  ├─ Teable REST append progress_log（带 command_id）
  ├─ sd_app command 标记 committed + outbox 入队
  └─ 返回 201 + submission_id

worker/outbox ──> 刷新 task 派生状态 ──> OA 待办回写 ──> 审计/对账
```

Teable 与 `sd_app` 无法共享本地事务，因此采用可恢复 saga：若 Teable 写成功但 command 未提交，对账任务按 `command_id` 找回并完成；所有步骤必须幂等。

## 5. 催办与 OA outbox

```text
APScheduler tick
  │ PostgreSQL advisory lock（同一 job+计划时刻唯一）
  ▼
rules(task snapshot, now, config, calendar) ──纯函数──> actions
  │
  ▼
outbox_message INSERT UNIQUE(dedup_key)
  │ commit
  ▼
worker claim(SKIP LOCKED) ──> OAClient ──> success / retry / dead_letter
                                      │
                                      └─ 每次 attempt 脱敏落库 + 告警
```

Redis 锁只能减少竞争；PostgreSQL 唯一约束和行锁才是正确性边界。OA 超时按“结果未知”处理，先用业务幂等键查询/重试，不能直接生成新 taskId。

## 6. AI 审读链路

```text
progress_log + task goal + approved history
  │ 数据最小化、账号字段剔除、内容用 data block 分隔
  ▼
prompt_version + model_version + JSON Schema
  │
  ▼
vLLM response_format=json_schema
  │
  ├─ Schema/Pydantic fail → 未审读 + 可重试
  ├─ confidence below threshold → 人工复核
  └─ valid → ai_run + progress_log.ai_*（建议）
                               │
                               └─ 督导办确认后才能发追问
```

用户文本和附件始终是不可信数据。LLM 无工具权限，不执行文本中的指令，不直接发送 OA，不签发报告。

## 7. 身份与授权

- JWT 只保存 `sub(person_id)`、`sid`、`iat/exp`、`kid`；不把 8 小时不变的 role/unit_scope 当权威。
- 中间件从 `authz:{person_id}` 缓存读取当前角色与范围，TTL 5 分钟；缓存 miss 回源 Teable 角色/组织关系并校验 `auth_session`。
- 角色或组织变更触发缓存失效；会话可按 `sid` 或用户撤销，不通过轮换全局密钥踢单人。
- 每个 service 查询函数必须显式接受 `AuthContext`，在远端查询/SQL 构造阶段过滤。

## 8. 降级矩阵

| 故障 | 自动行为 | 用户体验 | 数据保证 |
|---|---|---|---|
| GPU/LLM 不可用 | AI 任务重试后标 `degraded`；催办模板继续 | 显示“AI 暂不可用”，允许人工审读 | 不丢填报/催办 |
| OA 不可用 | outbox 重试，超过阈值进 dead letter | 显示待发送，运维可补发 | 不重复创建新业务键 |
| Teable API 不可用 | 查询仅用 5 分钟缓存；写拒绝并保留 command | 明确 503，可安全重试 | 不假装提交成功 |
| Redis 不可用 | 权限/会话回源 PG/Teable；停止非必要缓存 | 延迟上升，有告警 | 幂等/outbox 不受影响 |
| PostgreSQL 不可用 | API readiness 失败，停止写入与 worker claim | 维护提示 | 不接受无法审计的写入 |
| Webhook 丢失 | hourly reconciliation 拉取变更 | 无感或短时延迟 | 1 小时内收敛 |
| 文件扫描不可用 | prod 拒绝新附件，可提交无附件文本 | 明确说明扫描服务不可用 | 不接收未扫描文件 |

## 9. 容量与扩展

一期按百项级任务、周流水不超过 500 条、50 并发设计。单 APP-SRV 足够，但 API/worker 分进程，未来可分别扩容。只有出现持续资源证据后才引入外部消息队列或 Kubernetes。

