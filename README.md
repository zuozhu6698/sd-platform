# sd-platform

集团重点工作督导数字化平台。当前仓库是**已通过本地门禁的工程基线**；核心业务闭环和外部 POC 尚未完成，也未部署。

## 当前状态

- 工程文档：已完成 2026-07-23 架构/数据/安全/交付终审基线。
- Git：已建立独立本地仓库和 `codex/g0-engineering-baseline` 分支；origin 已配置为 `zuozhu6698/sd-platform` 个人 public 仓库，可使用 GitHub Free 的 public 仓库分支保护。
- 运行态：未部署。应用服务器已完成只读核验，但尚不满足目标 Compose 栈的直接部署前提；内部资产明细不进入 public 仓库。
- 工程门禁：当前 B8 审读任务分支后端 Ruff/format/Mypy/526 tests（总覆盖率 96.68%，AI schema/流水线/持久化/审读任务及既有核心模块 100%）本地通过；前端既有 lint/typecheck/11 tests/build（被测模块四项覆盖率 100%）通过；文档链接与 secret shape 检查通过。
- 核心底座：JWT/CSRF/重定向防护、SSO stub、动态权限、本人任务、安全填报、纯规则引擎、durable outbox、唯一调度、催办/对账/调度管理、OA 离线替身和严格 AI 核心已实现；当前分支新增未审读流水快照、任务/重点工作/近四期历史关联、逐条安全降级、结果与 `ai_run_id` 幂等写回。人工追问/回复/申诉、周月报 handler、其余三类 handler、全量运行时注册和真实外部联调仍未完成。
- 远程证据：PR #14 合并提交 `b42bef3` 的 GitHub Actions docs/backend/frontend/images 四个 job 全绿。当前审读任务增量位于 `codex/b8-ai-review-job`，尚待独立 PR 门禁。
- GitHub 治理：PR #1 经 docs/backend/frontend/images 四项 checks 全绿后 squash merge；`main` 已设为默认并启用严格保护规则。
- 下一步：完成应用机容量/运行时决策；OA 按项目负责人要求暂缓，先推进其余闭环。

完整状态与阻塞项见 [docs/00-project-status.md](docs/00-project-status.md)。

## 先读什么

1. [AGENTS.md](AGENTS.md)，仓库规则与文档权威顺序。
2. [docs/01-architecture.md](docs/01-architecture.md)，系统边界与数据流。
3. [docs/03-database.md](docs/03-database.md)，领域表与应用表。
4. [docs/07-security.md](docs/07-security.md)，身份、权限和审计。
5. [docs/12-implementation-plan.md](docs/12-implementation-plan.md)，实施顺序。

## 开发闭环

```text
任务卡 → codex/<id>-<slug> → 测试先行 → 实现 → 文档同步
      → 洁癖收尾 → PR → CI → squash merge main → 制品 → 部署 → 验收
```

仓库为 public，首次引导 `main` 和 CI 后必须启用 required pull request 与 required status checks；本地 pre-push hook 和 AGENTS 规则继续作为防误操作控制。

## 关键决策

- 正式填报使用自研 H5/PC 表单，经 BFF 写 Teable；普通用户不使用 Teable 共享表单。
- API 与 worker 分进程、同镜像；PostgreSQL durable outbox 保证 OA/AI 外部动作可重放。
- Teable 保存 9 张领域表；`sd_app` schema 保存会话、审计、幂等、任务运行、报告版本和 AI 溯源。
- Redis 分为 Teable 与应用两个实例；Redis 故障不能破坏正确性。
- AI 不参与确定性决策，且所有输入视为不可信数据。

## 未经允许不要做

不要连接生产 OA、不要登录服务器、不要创建远程仓库、不要写真实密钥、不要部署占位镜像。具体禁令见 [docs/08-prohibitions.md](docs/08-prohibitions.md)。
