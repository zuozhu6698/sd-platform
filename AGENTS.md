# AGENTS.md — 集团重点工作督导数字化平台（sd-platform）

本文件是仓库级编码规则。每次任务先读本文件，再按任务读取 `docs/` 对应分册。当前仓库处于“工程基线设计完成、代码未创建”阶段，状态以 `docs/00-project-status.md` 为准。

## 1. 项目定位

集团内网督导平台。Teable CE 保存督导业务数据，FastAPI 提供唯一业务入口，Vue 3 提供 PC/H5 页面，独立 worker 执行催办、OA、AI 和对账，观远 BI 只读访问稳定的 `bi` 视图。

## 2. 权威来源与阅读顺序

| 优先级 | 文档 | 负责回答 |
|---:|---|---|
| 1 | `docs/08-prohibitions.md` | 什么绝对不能做 |
| 2 | `docs/00-project-status.md` | 当前真实阶段、已确认事实、阻塞项 |
| 3 | `docs/07-security.md` | 信任边界、身份、权限、审计、敏感数据 |
| 4 | `docs/01-architecture.md` | 组件边界、关键数据流、降级策略 |
| 5 | `docs/03-database.md` | 业务表、应用表、指标口径、读写边界 |
| 6 | `docs/04-api-contracts.md` | HTTP/API 契约与错误语义 |
| 7 | `docs/02-tech-stack.md` | 技术栈、版本策略、依赖准入 |
| 8 | `docs/05-modules.md` | 可交付任务卡与依赖 |
| 9 | `docs/09-testing.md` | 测试矩阵和完成定义 |
| 10 | `docs/10-git-delivery.md` | Git、PR、CI、发布和回滚 |
| 11 | `docs/11-deployment-runbook.md` | 应用机部署、备份、恢复和运维 |
| 12 | `docs/12-implementation-plan.md` | 实施阶段、并行工作流、里程碑 |
| 13 | `docs/13-decisions-and-open-items.md` | 已定决策与待外部确认项 |

上级 DOCX 和 HTML 原型是背景与视觉证据，不是工程契约。若与本仓库冲突，以本表优先级为准；但涉及业务制度、权限、数据口径或上线范围的冲突必须记录到 `docs/13-decisions-and-open-items.md`，由业务/安全/运维责任人签字后才能实现。

## 3. 目标仓库布局

```text
sd-platform/
├── AGENTS.md
├── README.md
├── docs/
├── .github/workflows/
├── migrations/              # Alembic: sd_app + bi；不改 Teable 内部 schema
├── deploy/                  # nginx、监控、备份、环境模板
├── sd-agent/                # 同一镜像，sd-api 与 sd-worker 两种启动命令
└── web/                     # Vue 3 PC/H5
```

目录尚不存在时，先完成 `docs/05-modules.md` 的 G0/B0/F0，不得把文档里的目标命令当成“已验证”。

## 4. 工作流

1. 从 `docs/05-modules.md` 领取一个任务卡，创建 `codex/<卡号>-<slug>` 分支。
2. 先写失败测试，再实现；契约、schema、规则与代码在同一 PR 更新。
3. 只提交任务卡范围内文件。若必须扩展范围，在 PR 写明理由和新增验收项。
4. 运行 `docs/09-testing.md` 对应门禁，粘贴命令与结果；未运行写“未运行”，禁止写“应该通过”。
5. 运行洁癖收尾，对齐 README、AGENTS、docs、代码和当前状态，再创建 PR。
6. CI 全绿后 squash merge。public 仓库必须启用 GitHub branch protection；任何人/Agent 都禁止绕过规则直推 main。

## 5. 架构铁律

- 普通用户所有读写都走 BFF。正式环境禁止 Teable 共享表单直填。
- Teable REST 只承载领域数据；`sd_app` schema 承载会话、审计、幂等、outbox、调度、报告版本和 AI 溯源。
- `sd-api` 不运行调度；`sd-worker` 执行调度/outbox。Redis 仅作缓存和加速，不是正确性的唯一依据。
- LLM 只生成文本/结构化建议，不决定催办对象、升级级别、豁免、问责或报告签发。
- 任何外部动作先落 durable command/outbox，再异步发送；必须可重放、可对账、可人工补偿。
- 禁止直接 SQL 写 Teable 内部表。BI 只读 `bi.*`；视图变更必须版本化和兼容测试。

## 6. 编码与测试约定

- 后端 Python 3.11，FastAPI、Pydantic v2、SQLAlchemy Core + Alembic；全量类型标注，ruff。
- 前端 Node 24 LTS、Vue 3.5、Vite 8、TypeScript strict、pnpm；所有请求经 `web/src/api/`。
- `rules/` 为纯函数，禁止 IO、随机和直接读取当前时间。
- 代码、提交、文档使用中文说明；标识符使用英文；时区统一 `Asia/Shanghai`。
- 核心模块（权限、规则、幂等、outbox、审计）分支覆盖率 100%；项目总体分支覆盖率不低于 85%。
- 禁止调试输出、失效 TODO、裸异常栈、未校验 LLM 输出、`v-html`/`innerHTML` 注入业务文本。

## 7. 当前明确不做

- L3 业务系统自动取数、对话式查询和自动问责。
- 移动原生 App；一期只做 OA/M3 内嵌 H5。
- Kubernetes、多节点微服务拆分、消息中间件集群。单应用机阶段使用 Compose + PostgreSQL durable outbox。
- 在生产机 `git pull` 后现场构建。生产只接收 CI 生成并按 commit SHA/digest 标识的制品。
