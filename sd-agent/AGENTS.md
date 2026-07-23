# AGENTS.md — sd-agent

先读根 `AGENTS.md`、`docs/00-project-status.md`、`docs/07-security.md` 和 `docs/08-prohibitions.md`。

## 目录

```text
sd-agent/
├── pyproject.toml / uv.lock
├── alembic.ini / migrations/
├── config/rules.yaml / prompts/
├── sd_agent/
│   ├── api/ auth/ policies/ services/
│   ├── rules/ metrics/
│   ├── adapters/{teable,oa,llm,file_scan}/
│   ├── worker/{scheduler,outbox,reconciliation}/
│   ├── persistence/ audit/ models/
│   ├── api_main.py
│   └── worker_main.py
└── tests/
```

## 分层

`api → service → policy/rules/adapter/repository`。API 只做协议、service 负责编排、policy/rules 为纯函数、adapter 隔离外部差异、repository 只访问自有 `sd_app/bi`。

- API 禁止直接调 Teable/OA/LLM；外部动作先写 command/outbox。
- `rules/`、`policies/`、`metrics/` 禁止 IO、随机和直接读时钟。
- SQLAlchemy 只写 `sd_app/bi`；领域数据写经 Teable adapter。
- API 与 worker 同镜像不同入口，API 进程禁止启动 scheduler。
- 每个请求显式传 `AuthContext` 和 `request_id`。

## 正确性

- Idempotency-Key + payload_hash；同 key 不同 payload 必须 409。
- outbox/job/webhook 以 PG unique/lock 为正确性，Redis 只加速。
- 外呼 timeout 是“结果未知”，禁止直接换业务键重发。
- 所有 datetime 带 `Asia/Shanghai` 时区；计划任务同时保存 `scheduled_for`。
- LLM 必须 JSON Schema + Pydantic，来源 ID 必须属于输入白名单。

## 测试

核心模块分支覆盖 100%。测试禁止真实外呼；adapter 用 respx，PG/Redis 用 testcontainers。每个生产故障路径必须有测试或 TEST 演练证据。

