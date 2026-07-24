# 04 API 契约

## 1. 通用约定

- Base path：`/api`；JSON 字段使用 `snake_case`。
- 成功响应：`{"data": ..., "request_id": "..."}`。
- 错误响应：HTTP 语义状态码 + `{"error":{"code":"TASK_FORBIDDEN","message":"可安全展示的中文说明","details":{}},"request_id":"..."}`。
- 禁止“业务错误 HTTP 200”。使用 400/401/403/404/409/422/429/502/503。
- Cookie：`sd_token`，HttpOnly、Secure、SameSite=Lax、Path=/；状态写请求同时要求 `X-CSRF-Token`。
- 所有写接口支持/要求 `Idempotency-Key`；同 key 不同 payload 返回 409。
- 时间使用 RFC 3339 带时区；业务日期使用 `YYYY-MM-DD`。
- 列表使用 cursor 优先；确需 page/size 时上限 100。
- 所有响应都带 `X-Request-Id`；外部系统日志只记录脱敏 request_id/业务键。

## 2. 身份与 SSO

| 端点 | 方法 | 权限 | 说明 |
|---|---|---|---|
| `/api/sso/oa/start?redirect=/path` | GET | 公开 | 校验相对路径白名单，生成 state/nonce，跳 OA |
| `/api/sso/stub/authorize?state=&nonce=` | GET | 仅非生产离线模式 | 生成一次性测试 ticket 并回跳 callback；生产配置强制拒绝 stub |
| `/api/sso/oa/callback?ticket=&state=` | GET | 公开 | 校验 state、一次性 ticket、人员 active，创建 session，302 白名单路径 |
| `/api/logout` | POST | 已登录 | 撤销当前 sid、清 Cookie、落审计 |
| `/api/me` | GET | 已登录 | 当前人、有效角色/范围、能力位、CSRF token |

`redirect` 只接受 `/` 开头的站内相对路径，禁止 scheme、host、`//`、编码绕过。JWT claims 仅含 `sub/sid/kid/iat/exp`。

Wave 1 使用 `SSO_MODE=stub` 完成离线闭环：state/nonce 仅以 SHA-256 保存，state 五分钟过期，ticket 仅可交换一次；成功登录在同一事务创建 session 与审计，失败登录在同一事务使已存在的 state 失效并只记录错误码和 state 哈希前缀，不记录原始 state、nonce 或 ticket。生产环境拒绝 stub；真实 OA SSO URL、票据交换和字段映射保持 `pending-EXT-03`。

## 3. 端点清单

| # | 端点 | 方法 | 权限/说明 |
|---:|---|---|---|
| 1 | `/api/home/summary` | GET | 当前范围首页与我的待办 |
| 2 | `/api/map/tree?parent=&cursor=&filters=` | GET | 范围内懒加载树 |
| 3 | `/api/task/{task_id}` | GET | 范围内事项聚合 |
| 4 | `/api/task/{task_id}/logs?cursor=` | GET | 范围内流水 |
| 5 | `/api/files` | POST multipart | 负责人/专人；类型、魔数、大小、病毒扫描 |
| 6 | `/api/files/{file_id}` | GET | 对象级鉴权下载，不返回 Teable 直链 |
| 7 | `/api/report/submit` | POST | 负责人/专人；正式填报唯一入口 |
| 8 | `/api/reply/{log_id}` | POST | 本人回复/申诉 |
| 9 | `/api/review/pending` | GET | 督导办待确认追问/申诉 |
| 10 | `/api/review/{log_id}/decision` | POST | 督导办确认/驳回/裁决 |
| 11 | `/api/weekly/{period}` | GET | 督导办、领导只读 |
| 12 | `/api/weekly/{period}` | PUT | 督导办保存/签发，乐观锁 |
| 13 | `/api/urge/summary` | GET | 当前范围催办台账 |
| 14 | `/api/admin/runs` | GET | `ops_admin/supervision_admin` |
| 15 | `/api/admin/runs/{job}/trigger` | POST | 手动触发，需幂等键和审计 |
| 16 | `/api/hooks/teable` | POST | webhook secret + 来源限制 + durable 幂等 |
| 17 | `/healthz` | GET | 存活，仅进程状态 |
| 18 | `/readyz` | GET | 就绪，PG/Teable 必需依赖；LLM/OA 为 degraded 字段 |
| 19 | `/api/admin/outbox/dead-letters?cursor=&limit=` | GET | `ops_admin/supervision_admin`；只返回安全元数据，不返回 payload/dedup_key |
| 20 | `/api/admin/outbox/{outbox_id}/replay-approvals` | POST | `supervision_admin`；审批原因、CSRF、幂等键与审计 |
| 21 | `/api/admin/outbox/{outbox_id}/replay` | POST | 不同人员的 `ops_admin`；消费审批、CSRF、幂等键与审计 |

## 4. 关键 Schema

### 4.1 当前用户

```json
{
  "data": {
    "person": {"person_id": 5, "name": "张三", "unit_id": 10},
    "roles": [{"role": "domain_owner", "scope_unit_id": 10}],
    "can": {
      "report": true,
      "review": false,
      "issue_report": false,
      "outbox_view": false,
      "outbox_replay_approve": false,
      "outbox_replay_execute": false
    },
    "csrf_token": "<memory-only>"
  },
  "request_id": "req_..."
}
```

### 4.2 填报

Header：`Idempotency-Key: <uuid>`、`X-CSRF-Token: ...`

```json
{
  "task_id": 101,
  "content": "本周完成……",
  "progress": 65,
  "file_ids": ["file_..."],
  "on_behalf_of": null,
  "task_revision": 7
}
```

服务端从 session 决定 reporter，不能接受客户端提交 reporter/unit/role。成功返回 HTTP 201：

```json
{"data":{"submission_id":"cmd_...","log_id":9,"state":"committed"},"request_id":"req_..."}
```

错误：

- `REPORT_TOO_SHORT` 422；
- `PROGRESS_REGRESSION_REQUIRES_REVIEW` 409；
- `TASK_FORBIDDEN` 403；
- `TASK_STALE_REVISION` 409；
- `IDEMPOTENCY_CONFLICT` 409；
- `FILE_NOT_CLEAN` 422；
- `TEABLE_UNAVAILABLE` 503，可用同一 key 安全重试。

### 4.3 文件上传

上传必须先扫描，只有 `state=clean` 的 file_id 可进入填报。prod 扫描服务不可用时返回 503，不接收“稍后再扫”的附件。ZIP 一期禁止。

### 4.4 地图树

```json
{
  "data": {
    "nodes": [{
      "id":"kw-3","kind":"group|kw|unit|task","label":"安全生产专项",
      "badge":{"units":4,"tasks":23,"warns":3,"overdue":2},
      "task":{"deadline":"2026-09-30","progress":60,"status":"关注","ai_flag":"进度风险"},
      "has_children":true
    }],
    "edges":[{"from":"t-101","to":"t-205","kind":"related"}],
    "next_cursor":null
  },
  "request_id":"req_..."
}
```

### 4.5 报告保存与签发

```json
{"content_md":"...","action":"save|issue","audience":"leader|department","revision":4}
```

服务端净化 Markdown，忽略脚本/HTML；revision 不匹配返回 409。`issue` 创建不可变的新 report_version 并经 outbox 推送 OA，不能覆盖历史签发版。

### 4.6 人工裁决

```json
{"action":"push_question|dismiss|appeal_accept|appeal_reject","question_override":"可选","revision":2}
```

每个 action 有明确状态机；重复决定返回既有结果或 409，禁止静默覆盖他人裁决。

### 4.7 Dead letter 人工补发

审批与执行是两个独立写请求，均要求 `Idempotency-Key: <uuid>` 和 `X-CSRF-Token`。审批原因 10–500 字；审批人与执行人必须是不同人员。执行成功只把消息置为 `retry`，不代表外部 OA 动作已经成功；最终结果仍以新的 `outbox_attempt` 为准。

常见错误：`OUTBOX_FORBIDDEN` 403、`OUTBOX_TWO_PERSON_REQUIRED` 403、`OUTBOX_NOT_FOUND` 404、`OUTBOX_ALREADY_APPROVED` 409、`OUTBOX_APPROVAL_CONSUMED` 409、`IDEMPOTENCY_CONFLICT` 409。

## 5. Webhook

- 若 Teable 支持 HMAC：签名覆盖原始 body + timestamp，允许时钟偏差 5 分钟。
- 若只能静态 secret：必须叠加内网来源 allowlist、TLS、`event_id` 唯一和 payload hash。
- 先写 `webhook_receipt` 再返回 202；实际处理由 worker 异步完成。
- BFF 自己写入产生的事件按 `command_id` 对账，不重复触发 OA 动作。

## 6. 外部 Adapter 契约

### OAClient

`get_token()`、`send_pending()`、`complete_pending()`、`send_message()`、`exchange_ticket()`。token/password 可能受既有 OA 接口限制出现在 URL，adapter 必须关闭 URL 级日志并对代理/APM 做脱敏。

Wave 1 当前仅固化 `complete_pending(command_id, task_id, person_id, log_id, dedup_key)` 契约和 OA mock；mock 能复现同键冲突、429、5xx、业务拒绝及“服务端已接收但客户端超时”。真实接口认证和字段映射保持 `pending-EXT-03`，不得从 mock 推断厂商 contract。

### TeableClient

按逻辑表名/字段映射操作，不允许 service 保存 tableId。字段白名单、分页、重试、429/5xx 行为由 contract test 固定。

### LLMClient

使用 OpenAI-compatible `/v1/chat/completions`，以 `response_format=json_schema` 约束输出，再经 Pydantic 二次校验。实现不得使用已移除的 vLLM guided 字段。

## 7. 超时和重试

| 依赖 | connect/read | 重试 | 说明 |
|---|---|---|---|
| Teable GET | 3s/5s | 2 次 jitter | 写请求只按 idempotency key 重试 |
| OA | 3s/8s | outbox 最多 6 次指数退避 | 超时视为结果未知 |
| LLM | 3s/20s | batch job 2 次 | API 请求不同步等待批审读 |
| 文件扫描 | 3s/30s | 1 次 | 失败拒绝附件 |

## 8. 兼容与变更

API 初始版本为 `/api`，首个外部 consumer 稳定后升级为 `/api/v1`。任何破坏性变更必须先加新字段/端点，完成双写/consumer 迁移后再删除；同一 PR 更新后端 schema、前端类型、contract tests 和本文。
