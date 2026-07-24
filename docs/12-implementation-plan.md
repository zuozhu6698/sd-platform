# 12 实施计划

## 1. What already exists

| 已有资产 | 复用方式 |
|---|---|
| 业务方案与 6 册详设 DOCX | 作为 Why/业务背景和联调线索，不直接当工程契约 |
| 多个 HTML 高保真原型 | 提取信息架构、视觉 token 和交互意图；不复制 CDN/innerHTML/固定布局代码 |
| 本 docs/ 与 AGENTS | 作为工程 source of truth |
| 现有 OA、观远 BI | 只做 adapter 与联调，不开发本体 |
| APP-SRV | 通过 O0 后用于 TEST/生产单机 Compose |

已有 G0/B0/F0 工程基线、显式数据库迁移、单元测试、锁文件和 CI 工作流；尚无可复用的核心业务闭环、POC 报告或生产制品。

## 2. 范围原则

最小目标不是“把所有页面做出来”，而是证明这条链不丢数据、不越权、可追责：

```text
SSO → 本人任务 → 安全填报 → 规则催办 → OA 回执 → 审计/对账
```

AI、复杂地图、BI 驾驶舱在最小闭环稳定后接入。

## 3. 阶段与 Gate

| 阶段 | 任务卡 | 交付物 | 退出条件 |
|---|---|---|---|
| M0 基线 | G0/G1/B0/F0 | public repo、受保护 main、CI、空工程、锁文件、文档门禁 | Gate 1 |
| M1 POC/底座 | P0/P1/P2/O0/B1/B2 | 本地替身契约、9 领域表、sd_app/bi 迁移；真实 POC 按 §4b 进入 Wave 3 | Gate 2-offline |
| M2 最小闭环 | B3/B4/B5/B6/B7/F1/F2/J0 | SSO/填报/催办/OA/审计 | Gate 3 |
| M3 AI/报告 | B8/B9/F3/J1 | AI 审读、人工追问、报告签发/溯源 | eval 达标 |
| M4 UI/运维 | F4/O1/O2/J2 | 地图、工作台、BI、TEST 部署、恢复/性能/安全 | Gate 4 |
| M5 试点 | J3 | 两周双轨、对账、培训、签字 | Gate 5 |
| M6 生产 | 发布卡 | 不可变制品、release marker、live smoke | Gate 6 |

## 4. 依赖与并行 Lane

| Lane | 顺序 | 模块 | 依赖 |
|---|---|---|---|
| A 治理/后端底座 | G0 → B0 → B1 → B2 | repo、sd-agent、migrations | — |
| B 外部 POC | P0 + P1 + P2 | Teable/OA/GPU | G0，可并行 |
| C 前端骨架 | F0 → F1 | web | G0；API mock 可先行 |
| D 运维 | O0 → O1 | deploy/infra | G0；O1 等 B0/F0 |
| E 核心业务 | B3 → B4；B5 → B6 → B7 | auth/report/rules/worker/OA | A + P0/P1 |
| F 用户界面 | F2 → F3 → F4 | report/workbench/map | C + 对应 API |
| G AI/报告 | B8 → B9 | llm/report/metrics | P2 + B6 |
| H 验收 | J0 → J1 → J2 → J3 | tests/runbooks | 上游阶段 |

执行顺序：M0 后同时启动 A/B/C/O0；合并接口/迁移基线后启动 E/F；G 不阻塞 J0；最后 H。并行 worktree 不能同时改 `docs/04`、`docs/03` 或共享迁移头，契约/迁移变更由 Lane A 串行合并。

## 4b. Offline-first 波次与外部依赖纪律

外部依赖未到位时不得空等，也不得用不可运行的占位代码冒充完成。开发按以下三波推进：

| Wave | 必做范围 | 允许的本地证据 | 完成条件 |
|---|---|---|---|
| Wave 1 核心闭环 | B1-B7、F1-F2、J0；SSO→本人任务→安全填报→规则催办→OA mock 回执→审计/对账 | SSO stub、本地 Teable、OA mock、ClamAV 替身；真实 adapter 保留同一契约 | 自动化 E2E trace、权限矩阵、outbox fault injection、对账零差异；核心链路不依赖 GPU |
| Wave 2 产品闭环 | B8-B9、F3-F4、J1；AI 审读/人工追问/周报、管理端、对齐地图、统计展示 | 本地小模型/确定性 LLM stub、固定数据集、BI 只读契约测试 | AI 输出经 schema/溯源/人工裁决；页面空/慢/错/无权限齐全；100+ 事项性能证据 |
| Wave 3 外部联调/部署 | P0-P2、O0-O2、J2-J3 和真实 OA/GPU/观远/Harbor/APP-SRV 验收 | 只允许 adapter 骨架、contract mock 和用例清单先行 | 对应 EXT 到位后取得真实 POC/TEST/恢复/签字证据；未取得不得标记完成 |

Wave 3 外部依赖标记：

| 依赖 | 未到位时允许提交 | 禁止宣称 |
|---|---|---|
| EXT-01 GPU/集团模型服务 | LLM adapter、JSON Schema、固定 eval、小模型替身；标 `pending-EXT-01` | 模型质量、吞吐或 SLA 已验收 |
| EXT-03 OA/SSO TEST | OA/SSO adapter、mock server、重放/回执/超时 10 用例；标 `pending-EXT-03` | 真实 SSO、消息送达或 OA 幂等已验证 |
| EXT-04 观远 | `bi.*` 稳定视图、只读账号契约、样例查询；标 `pending-EXT-04` | 观远连通、刷新或看板验收 |
| EXT-05 Harbor/Nexus | 固定 digest 的 Dockerfile、SBOM/扫描工作流；标 `pending-EXT-05` | 内网制品推送、保留或回滚已验证 |
| EXT-07 APP-SRV | Compose/Nginx/监控/备份脚本和本机 config 测试；标 `pending-EXT-07` | TEST/生产容量、部署、恢复或 live 可用 |

执行纪律：

1. 每张卡从 `codex/<card>-<slug>` 分支经 PR、required CI 全绿后 squash merge；PR 必须注明 Wave 和依赖的 EXT 编号。
2. 契约、表结构、阈值与代码在同一 PR 更新 docs、迁移和测试；每批完成后更新 `docs/00-project-status.md`。
3. Wave 1/2 的替身必须走与真实 adapter 相同的 service 接口，测试禁止真实外呼；禁止空 handler、永远成功的假回执和绕过权限的 fixture。
4. Wave 1/2 全部清零并通过 Gate 3 后停止开发，向项目负责人提交证据与 Wave 3 `pending-EXT-*` 清单；未获确认不得进入部署、TEST 或生产验收。
5. Gate 3 是“本地最小闭环自动自测通过”，不替代 Gate 2 的真实外部 POC，也不证明 Gate 4/5/6。外部证据在 Wave 3 补齐后才能推进相应 Gate。

## 5. 每阶段证据

- M0：remote、branch、CI URL、锁文件、secret scan。
- M1：POC 报告、schema revision、Teable version/digest、OA 10 用例、GPU benchmark、资产/网络矩阵。
- M2：E2E trace、权限矩阵、outbox fault injection、对账报告。
- M3：200 条 eval、prompt/model version、人工复核样本、来源准确性。
- M4：性能 trace、漏洞/SBOM、恢复计时、故障演练、移动/可访问性截图。
- M5：双轨自动对账、培训签到、业务/安全/运维签字。
- M6：release marker、相同 digest 推进、live smoke、回滚观察窗。

## 6. NOT in scope

- L3 自动取数/对话查询：需新的业务系统合同，不能混入一期。
- 自动问责/自动签发：组织责任必须由人承担。
- 原生移动 App：OA/M3 H5 已覆盖一期旅程。
- Kubernetes/外部 MQ：当前单机百项级规模没有证据支持。
- 高可用多机：先做可恢复单机；达到容量/SLO阈值后另立项。
- Teable 共享表单正式填报：无法满足 BFF 身份绑定和权限审计。
- 重写 Teable/观远/OA：均为外部系统，只做适配。

## 7. 风险与退出策略

| 风险 | 早期验证 | 退出/替代 |
|---|---|---|
| Teable REST/升级不稳定 | P0 | 自有 Postgres 领域层另立 ADR；不在实施中途偷偷改 |
| OA 接口幂等/SSO差异 | P1 | adapter + 人工消息降级；阻塞 M2 |
| GPU/32B 不可用 | P2 | 集团模型服务或 8B TEST 替身；AI 不阻塞 J0 |
| APP-SRV 资源不足 | O0/TEST load | 扩容/拆 GPU；不牺牲备份和安全 |
| branch protection 被误关 | hook+审计+定期 API 核验 | 立即恢复规则并审计直推记录 |

## 8. 估算方式

不再用一份 63/65 人日总数冒充承诺。G0 后由任务卡按“开发+测试+评审+文档+部署证据”重新估算，并以实际 velocity 每周校准。外部 POC 和责任方等待时间单独记录，不能混入编码工时。
