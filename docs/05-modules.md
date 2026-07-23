# 05 模块与任务卡

每张卡是一个可独立验收的 PR。领取前确认依赖，完成时执行 `docs/09-testing.md` 的 DoD。

## G. 工程治理

### G0 仓库与 CI 基线

- 创建独立 Git 仓库、README、ignore/attributes、`codex/*` 分支流程、本地 main pre-push guard。
- 建立后端/frontend/compose/docs CI 占位与 secret scan；锁 GitHub Actions SHA。
- 验收：public remote 正确；无真实 secret；空工程 CI 可重复通过；main 启用保护且无直接提交。

### G1 文档与决策门禁

- 文档链接/引用检查；schema/API 变更必须同步 docs；ADR/开放项模板。
- 验收：破坏一个引用或制造 API diff 时 CI 必须失败。

## B. 后端

### B0 FastAPI 工程骨架

- `sd-api`/`sd-worker` 两入口、配置校验、structlog、request-id、错误封装、health/readiness、Dockerfile。
- 验收：prod 缺密钥/镜像配置启动失败；API 与 worker 不加载对方运行职责。

### B1 sd_app schema 与迁移

- Alembic 建 `auth_session/file_object/submission_command/webhook_receipt/audit_event/job_run/outbox_message/outbox_attempt/report_version/ai_run`。
- 验收：空库 upgrade、已有库重复 upgrade、downgrade/备份策略、权限越界测试。

### B2 Teable adapter 与领域建模

- 已实现 9 张领域表映射、字段白名单、安全 REST CRUD、`command_id` 对账和本人任务读取；仍需 Webhook receipt、批量过滤 POC 与 bi views contract test。
- 验收：禁止 SQL 写 Teable；白名单外字段拒绝；Teable 版本 POC 报告。

### B3 SSO、会话与 RBAC

- OA start/callback、state/redirect、JWT kid、session revoke、5 分钟 authz cache、多角色/范围策略、CSRF。
- 验收：角色矩阵、ticket 重放、开放重定向、缓存失效、单会话撤销全部自动测试。

### B4 安全填报与文件

- 已实现附件元数据、真实类型/OOXML 宏检查、同步扫描 fail-closed、隔离、SHA-256、PostgreSQL 元数据 adapter、原子对象存储、HTTP 扫描器契约、上传下载 API、对象级下载授权、submission saga、幂等、并发 revision 和异常恢复；仍需真实扫描 sidecar POC、任务参与者下载授权与进度更正审批端点。
- 验收：双击、两标签、Teable 写后进程崩溃、恶意附件、扫描不可用、跨组织 task_id 均覆盖。

### B5 规则引擎

- 纯函数状态机、工作日日历、临期/到期/逾期/催报、豁免、频控、合并、权重进度。
- 验收：分支覆盖率 100%，参数化不少于 80 例，随机/IO/直接时钟读取为零。

### B6 worker、scheduler 与 durable outbox

- 已实现填报完成 outbox 入队、`SKIP LOCKED` claim、租约、指数退避和 dead letter 核心；仍需 7 个计划任务、worker 消费循环、人工补发与对账端点。
- 验收：多 worker 竞争、进程崩溃、OA 超时结果未知、重复触发、Redis 下线不重发。

### B7 OA adapter

- token、待办、已办、消息、SSO 差异配置；完整 URL 脱敏；真实测试环境 10 用例。
- 验收：contract mock + OA TEST 报告；凭据不进日志/异常。

### B8 AI 流水线

- `ReviewResult/WeeklyDraft/UrgeText` schema、数据分隔、prompt/version、ai_run、降级、批量限流。
- 验收：非法 JSON、提示注入、虚构来源、超时、模型切换；评测门槛见 docs/09。

### B9 报告、审计与指标

- report_version 状态机、人工签发、audit middleware、指标函数、bi.week_snapshot。
- 验收：并发编辑 409、签发不可改、来源可追溯、BI/API 指标一致。

## F. 前端

### F0 Vue 工程骨架

- Node 24/Vite 8/Vue 3.5、路由、Pinia、typed API、Cookie/CSRF、错误边界、design tokens、Playwright。
- 验收：typecheck/unit/build/e2e smoke；无 CDN/远程字体/外网遥测。

### F1 SSO 与通用壳

- 静默登录、403/503/session-expired、空/慢/错/降级横幅、键盘/焦点、`embed=1`。
- 验收：跳转白名单、返回路径、错误恢复、200% 缩放。

### F2 安全填报 PC/H5

- 已实现响应式 `/report`、本人任务选择、附件上传/扫描、分段失败续传、重复提交锁和同幂等键安全重试；仍需独立 `/m/report` 触控验收、冲突引导与 Playwright E2E。
- 验收：375px 无横向滚动、触控目标≥44px、双击/离线/过期/跨权限 E2E。

### F3 工作台与台账

- `/home /mytasks /ai /workbench/report /ledger`，所有页面空/慢/错/无权限。
- 验收：能力位仅控制展示，后端仍拒绝越权；Markdown 净化测试。

### F4 对齐地图

- X6 懒加载、Worker 布局、筛选搜索、详情抽屉、简卡、minimap、键盘替代路径。
- 验收：100+ task 首次展开≤1s，拖拽≥40fps；H5 使用列表替代，不挤压桌面画布。

## O. 基础设施与交付

### O0 应用机资产与网络 POC

- 只读确认 CPU/RAM/disk/OS/Docker/DNS/CA/到 OA、BI、Harbor、Nexus、GPU 的连通性。
- 验收：资产清单和防火墙矩阵签字，不保存密码。

### O1 Compose、Nginx 与制品

- 网络分区、两 Redis、API/worker、健康检查、日志滚动、不可变镜像、Harbor 发布。
- 验收：TEST `docker compose config`、重启、磁盘/日志上限、非授权端口不可达。

### O2 备份、恢复、监控与回滚

- WAL+全量+离机副本、恢复演练、指标/告警、release marker、前后端/迁移回滚。
- 验收：RPO≤15min、RTO≤4h 的 TEST 实测报告。

## P. 外部 POC

### P0 Teable POC

9 表/REST/附件/Webhook/升级/bi 兼容。正式链路不评估共享表单直填。

### P1 OA POC

token、SSO、待办、已办、消息、M3 URL、日志脱敏、幂等和停机恢复。

### P2 模型 POC

GPU/统一模型服务、32B 候选、结构化输出、4 并发、200 条批审读、质量/显存/延迟/降级。

## J. 集成与试点

### J0 最小闭环

登录 → 查看本人任务 → 安全填报 → 规则催办 → OA 回执 → 审计/对账。

### J1 AI/报告闭环

审读 → 人工确认追问 → 回复/申诉 → 周报草稿 → 人工签发 → 来源回看。

### J2 TEST 验收

完整安全/性能/恢复/降级/兼容矩阵，全部留档。

### J3 双轨试点

与 Excel 并行两周，自动对账证明零漏发/零重发，业务签字后冻结 Excel。
