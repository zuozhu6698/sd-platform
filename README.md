# sd-platform

集团重点工作督导数字化平台。当前仓库是**已通过本地门禁的工程基线**；核心业务闭环和外部 POC 尚未完成，也未部署。

## 当前状态

- 工程文档：已完成 2026-07-23 架构/数据/安全/交付终审基线。
- Git：已建立独立本地仓库和 `codex/g0-engineering-baseline` 分支；计划创建 `zuozhu6698/sd-platform` 个人私有仓库，当前无 GitHub Pro。
- 运行态：未部署。应用服务器已确认，GPU 服务、域名、证书、OA 测试账号和内网制品库仍待确认。
- 工程门禁：后端 Ruff/format/Mypy/182 tests（总覆盖率 91.15%，纯规则模块 100%）通过；前端 lint/typecheck/5 tests/build（被测模块四项覆盖率 100%）通过；文档链接与 secret shape 检查通过。
- 核心底座：JWT/CSRF/重定向防护、PostgreSQL session 撤销、动态角色范围、`/api/me`、`/api/logout`、纯规则引擎、Teable 九表字段白名单与安全重试适配器已实现；OA start/callback 与真实 Teable/OA 联调仍以 POC 为准。
- 下一步：完成远程仓库/CI 后并行开展 P0/P1/P2 三项 POC，再进入最小业务闭环。

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

没有 GitHub Pro 时，私有仓库无法强制保护 `main`。仍必须走 PR；本地 pre-push hook 和 AGENTS 规则只是补偿控制，不等同于平台强制保护。

## 关键决策

- 正式填报使用自研 H5/PC 表单，经 BFF 写 Teable；普通用户不使用 Teable 共享表单。
- API 与 worker 分进程、同镜像；PostgreSQL durable outbox 保证 OA/AI 外部动作可重放。
- Teable 保存 9 张领域表；`sd_app` schema 保存会话、审计、幂等、任务运行、报告版本和 AI 溯源。
- Redis 分为 Teable 与应用两个实例；Redis 故障不能破坏正确性。
- AI 不参与确定性决策，且所有输入视为不可信数据。

## 未经允许不要做

不要连接生产 OA、不要登录服务器、不要创建远程仓库、不要写真实密钥、不要部署占位镜像。具体禁令见 [docs/08-prohibitions.md](docs/08-prohibitions.md)。
