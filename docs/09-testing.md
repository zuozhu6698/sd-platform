# 09 测试计划与完成定义

## 1. 质量门槛

| 层 | 工具 | 门槛 |
|---|---|---|
| 后端单测 | pytest | rules/authz/idempotency/outbox/audit 分支覆盖 100%；总体分支≥85% |
| 后端集成 | pytest + testcontainers + respx | API、PG、Redis、Teable/OA/LLM adapter contract |
| 前端单测 | Vitest | stores、API、错误恢复、地图布局、Markdown 净化 |
| E2E | Playwright | SSO stub、填报、双击/冲突、权限、AI/报告、PC/H5 |
| LLM eval | 版本化数据集 | schema valid 100%；来源 ID 准确 100%；高风险 recall≥90%；总体 precision≥85%、recall≥80% |
| 性能 | Locust + Playwright trace | BFF P95<500ms@50；地图 100+ task≤1s/≥40fps；outbox backlog 可恢复 |
| 安全 | 自动化 + TEST 部署 | docs/07 全矩阵，无 P0/P1 高危未处置 |
| 运维 | 恢复/降级演练 | RPO≤15min、RTO≤4h，回滚/补发有证据 |

50 条旧评测样本只作为 seed。Gate 4 前扩充至至少 200 条分层、双人标注、分歧仲裁的数据集；真实数据进入 TEST 前脱敏。

## 2. 关键代码路径覆盖图

```text
SSO
 start → redirect allowlist → state cookie → OA → callback
      ├─ valid ticket → session → authz cache          [unit + E2E]
      ├─ replay/expired/wrong state                    [integration]
      └─ open redirect/encoded bypass                  [security]

SUBMIT
 session → RBAC → CSRF → task revision → file clean → command INSERT
      ├─ Teable append → command commit → outbox       [integration + E2E]
      ├─ duplicate same payload → existing result      [integration]
      ├─ duplicate different payload → 409             [integration]
      ├─ crash after Teable write → reconciliation     [fault injection]
      └─ timeout/403/stale/scan failure → clear UX     [E2E]

OUTBOX
 schedule → PG lock → pure rules → unique outbox → claim → OA
      ├─ success → urge_log + attempt                  [integration]
      ├─ timeout unknown → same key retry              [integration]
      ├─ worker crash → lease recovery                 [fault injection]
      ├─ max retry → dead letter + alert               [integration]
      └─ Redis down → still exactly-once effect        [degradation]

SCHEDULER ADMIN
 session → role + CSRF + idempotency → trigger_request + outbox + audit
      ├─ same key/same payload → existing result       [integration]
      ├─ same key/different payload → 409               [integration]
      ├─ retry only matching failed run, once           [integration]
      └─ worker → SchedulerService → job_run             [unit + integration]

AI
 untrusted data → minimize/delimit → json_schema → Pydantic → ai_run
      ├─ valid + confident → suggestion                [unit + eval]
      ├─ invalid/timeout/low confidence → human        [unit + E2E]
      └─ injection/fake source/privacy attempt         [eval/security]
```

## 3. 必测用户旅程

1. OA 登录 → 本人任务 → H5 填报 → OA 已办 → 台账可见。
2. 双击提交、两标签提交、页面放置 30 分钟后提交，均不重复、不静默丢数据。
3. 部门专人代报，页面和审计明确显示实际提交人/代报对象。
4. 越权枚举 task/log/file ID，均 403 + audit_event。
5. AI 标记 → 督导办编辑/确认 → 负责人回复/申诉 → 裁决留痕。
6. 两人同时编辑周报，一人签发后另一人收到 409，不覆盖签发版。
7. GPU/OA/Teable/Redis 分别下线，用户看到可理解状态，主链路按降级矩阵运行。
8. H5 375px、200% 缩放、键盘操作、慢网/500/503/空数据均可恢复。

## 4. 安全用例

- SSO state、ticket replay、redirect 编码绕过、session revoke、JWT kid 轮换。
- CSRF 无 token/错 Origin，CORS 预检，CSP/XSS/Markdown/文件名注入。
- 多角色并集、scope 边界、代报、leader 写操作、ops 读取业务正文。
- Webhook 伪造、重放、payload hash 冲突；OA URL/token 日志扫描。
- EXE 改后缀、MIME 欺骗、宏文档、ZIP、超大文件、扫描超时、无权下载。
- Prompt 注入、账号外泄诱导、虚构 task ID、超长文本、Unicode 控制字符。
- 容器非 root（可行时）、只读文件系统、端口暴露、默认凭据、镜像漏洞/SBOM。

## 5. 故障模式

| 故障 | 必须有测试 | 错误处理 | 用户可见 |
|---|---|---|---|
| Teable 写成功、API 崩溃 | 是，fault injection | reconciliation 由 command_id 收敛 | 同 key 重试返回既有结果 |
| OA timeout 但实际已接收 | 是 | 同 dedup key 查询/重试 | 显示“发送确认中” |
| Redis 丢数据 | 是 | PG/session/Teable 回源 | 可能变慢，不丢状态 |
| work_calendar 缺日期 | 是 | job fail + alert，不猜 | 运维告警 |
| report 并发覆盖 | 是 | revision 409 | 提示刷新合并 |
| LLM 返回合法 JSON 但假来源 | eval | source ID 白名单校验 | 转人工，不展示为事实 |
| 文件扫描不可达 | 是 | prod 拒绝附件 | 可去掉附件重试 |
| 磁盘满/WAL 归档失败 | 演练 | readiness/告警/停止写 | 维护提示 |

任一故障若“无测试 + 无错误处理 + 静默影响用户”，即 P0，禁止进入 Gate 4。

## 6. CI 工作流

```text
docs: markdown/link/reference/secret checks
backend: uv sync --frozen → ruff → typecheck → pytest --cov
frontend: pnpm --frozen-lockfile → lint/typecheck/test/build
e2e: compose TEST dependencies → Playwright critical paths
security: secret scan → dependency/SBOM → container scan → IaC/Compose check
migrations: empty upgrade + previous-version upgrade + schema contract
```

Actions 使用 commit SHA 固定；PR job 名唯一。public 仓库通过 required status checks 强制阻断未通过 CI 的合并。

## 7. 每任务 DoD

- [ ] 任务卡范围和依赖明确；没有越界或已解释。
- [ ] 新逻辑先有失败测试；正常、边界、错误、并发、恢复均覆盖。
- [ ] API/schema/权限/阈值同步 docs 与 contract tests。
- [ ] 日志/错误/fixture 无真实敏感数据。
- [ ] 相关门禁命令实际运行并记录结果。
- [ ] 前端有空/加载/错误/无权限/降级状态与可访问性验证。
- [ ] 洁癖对齐 README、AGENTS、docs、实现和项目状态。

## 8. 里程碑验收

`M0 Git+CI → M1 底座/迁移 → M2 OA 最小闭环 → M3 自动督导/AI → M4 UI/BI/TEST → M5 双轨试点 → M6 生产发布`。各阶段证据清单见 docs/12。
