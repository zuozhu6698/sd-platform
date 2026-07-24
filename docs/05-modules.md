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

- Alembic 建 `auth_session/file_object/submission_command/webhook_receipt/audit_event/job_run/outbox_message/outbox_attempt/outbox_replay_approval/report_version/ai_run`。
- 验收：空库 upgrade、已有库重复 upgrade、downgrade/备份策略、权限越界测试。

### B2 Teable adapter 与领域建模

- 已实现 9 张领域表映射、字段白名单、安全 REST CRUD、`command_id` 对账和本人任务读取；仍需 Webhook receipt、批量过滤 POC 与 bi views contract test。
- 验收：禁止 SQL 写 Teable；白名单外字段拒绝；Teable 版本 POC 报告。

### B3 SSO、会话与 RBAC

- OA start/callback、state/redirect、JWT kid、session revoke、5 分钟 authz cache、多角色/范围策略、CSRF。
- Wave 1 已实现 SSO stub：站内 redirect 白名单、五分钟 state/nonce、一次性 ticket、防重放、active 人员校验、HttpOnly/Secure/SameSite Cookie、会话创建及成功/失败审计；生产环境强制拒绝 stub。
- 真实 OA 授权地址、ticket 交换与人员字段映射保持 `pending-EXT-03`，stub 证据不得作为真实 OA 联调结论。
- 验收：角色矩阵、ticket 重放、开放重定向、缓存失效、单会话撤销全部自动测试。

### B4 安全填报与文件

- 已实现附件元数据、真实类型/OOXML 宏检查、同步扫描 fail-closed、隔离、SHA-256、PostgreSQL 元数据 adapter、原子对象存储、HTTP 扫描器契约、上传下载 API、对象级下载授权、submission saga、幂等、并发 revision 和异常恢复；仍需真实扫描 sidecar POC、任务参与者下载授权与进度更正审批端点。
- 验收：双击、两标签、Teable 写后进程崩溃、恶意附件、扫描不可用、跨组织 task_id 均覆盖。

### B5 规则引擎

- 纯函数状态机、工作日日历、临期/到期/逾期/催报、豁免、频控、合并、权重进度。
- 验收：分支覆盖率 100%，参数化不少于 80 例，随机/IO/直接时钟读取为零。

### B6 worker、scheduler 与 durable outbox

- 已实现填报完成 outbox 入队、`SKIP LOCKED` claim、租约、指数退避、不可重试错误立即 dead letter、显式 handler 白名单、可优雅停止的 worker 消费循环，以及 dead letter 安全列表、督导审批/异人运维执行、幂等重放和事务审计。
- 已实现 7 类任务目录、APScheduler worker、上海时区计划槽、15 分钟 misfire、`job_run(job, scheduled_for)` 唯一键、PostgreSQL advisory lock、配置哈希、成功/失败计数和脱敏错误落账。任务 handler 必须 7 项完整注册，否则 `CRON_ENABLED=true` 启动即失败。
- 已实现运行记录安全列表、CSRF/角色保护的幂等手动触发与失败补跑：API 只在同一事务写 `job_trigger_request + scheduler.run_job outbox + audit_event`，由 worker 执行统一 scheduler；同 key 异 payload 409，一条失败 run 只可发起一次补跑。完整业务 handler 注册前该 outbox kind 不启用，避免 API 进程越权代跑。
- B6 子闭环已实现：Teable 任务/唯一主责人/完整工作日日历快照，纯规则生成固定催办正文，PG `dedup_key` 唯一入 outbox 与事务审计，OA mock 幂等发送后追加 `urge_log`；填报 command 超过 5 分钟未收敛时按 `command_id` 对账，命令级 PG advisory lock 防并发，进度只前进不回退，恢复提交与 OA 完成命令同事务落账。
- 离线验证阈值为临期 5 个工作日、逾期 3 个工作日升级、对账陈旧 5 分钟、单批 100；均有配置边界。它们尚待业务负责人确认，确认前仅用于 Gate 3 固定数据集，不得开启生产调度。
- 当前尚未把 7 类 handler 全部注册到运行时；`report_reminder/ai_review/weekly_report/monthly_report/weekly_snapshot` 仍待后续卡完成，因此 `CRON_ENABLED=false` 保持不变。原方案未定义 `task.cycle` 枚举、各周期截止规则和“本期已报”判定，催报 handler 不得自行猜测，见 OPEN-12。
- 现行计划目录：每日 09:00 催办扫描；周五 12:00 催报；周五 12:30 AI 审读；周五 14:00 周报与周快照；每小时第 5 分钟对账；月末 18:00 月报。原设计只明确月报“月末”，18:00 是禁用态配置基线，启用前必须由业务负责人确认。OA 暂停期间 `OUTBOX_ENABLED=false`。
- 验收：多 worker 竞争、进程崩溃、OA 超时结果未知、重复触发、Redis 下线不重发。

### B7 OA adapter

- Wave 1 已实现 `complete_pending` 与 `send_urge` durable outbox handler、有状态 OA mock、同业务键去重/冲突、限流/服务故障/业务拒绝、已接收但客户端超时后同键收敛，以及 HTTPS HTTP adapter 骨架；`OA_MODE=mock` 必须显式开启且生产环境强制拒绝。
- HTTP adapter 使用 header 凭据与幂等键，错误分类不复制响应正文、完整 URL 或凭据。真实致远 token、待办、已办、消息、SSO 字段映射和 TEST 10 用例保持 `pending-EXT-03`。
- 验收：offline contract mock 已通过；OA TEST 报告尚未取得，不得宣称真实送达。

### B8 AI 流水线

- 已实现 `ReviewResult/WeeklyDraft/UrgeText` 严格 Schema、提示与不可信数据分隔、输入体积边界、prompt version、来源 ID 白名单、周报 `[T{id}]` 精确对账、非法输出 fail closed、0.6 低置信度降级和 `ai_run` 成功/失败溯源。
- `LLM_MODE=mock` 仅在非生产环境显式启用保守确定性替身：审读不作风险判断、周报只输出带来源的事实占位，不能用其证明模型质量。生产拒绝 mock；真实 OpenAI-compatible adapter、模型评测和 GPU POC 保持 `pending-EXT-01`。
- 尚未完成 `ai_review/weekly_report/monthly_report` handler、Teable 结果写回、人工追问确认/回复/申诉和报告版本/签发 API；本批不把核心组件写成“业务闭环完成”。
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
