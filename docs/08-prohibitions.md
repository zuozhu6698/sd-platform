# 08 禁令清单

违反任一条即停止合并。确需例外时，PR 必须写明条款、理由、影响面、到期日、回收负责人，并获得业务/安全/运维相应责任人批准。

## A. 架构

| # | 禁令 |
|---|---|
| A1 | 普通用户禁止直连 Teable/PG/vLLM/OA；正式填报禁止使用 Teable 共享表单 |
| A2 | 前端禁止保存业务真相或凭据；UI 能力位禁止替代 BFF 鉴权 |
| A3 | sd-api 禁止运行 scheduler；worker 禁止提供用户业务接口 |
| A4 | 禁止直接 SQL 写 Teable 内部表；领域写只经 Teable REST |
| A5 | 禁止绕过 durable outbox 同步执行 OA 外部动作 |
| A6 | Redis 禁止作为幂等、审计、job、outbox 的唯一存储 |
| A7 | 禁止未有性能/运维证据就引入 Kafka/RabbitMQ/Kubernetes/服务网格 |
| A8 | 禁止实现一期外的 L3 自动取数、对话查询、自动问责 |

## B. 数据

| # | 禁令 |
|---|---|
| B1 | 单选/枚举值禁止原地改语义；废弃需迁移和兼容窗口 |
| B2 | progress_log、audit_event、outbox_attempt 禁止覆盖历史；纠错追加新记录 |
| B3 | 人员、组织、角色分配禁止物理删除业务历史；使用 active/valid_until |
| B4 | 禁止静默回退进度；真实更正必须追加 correction、理由与批准人 |
| B5 | 指标公式禁止在前端/BI/周报各实现一套；以 docs/03 和后端 metrics 为准 |
| B6 | 禁止测试数据、真实生产附件或未脱敏样本进入错误环境 |
| B7 | 禁止把 `docker-entrypoint-initdb.d` 当持续迁移；自有 schema 必须 Alembic 版本化 |
| B8 | 禁止依赖 Teable 多选顺序表达第一责任人；使用 task_owner.is_primary |

## C. 安全

| # | 禁令 |
|---|---|
| C1 | 密钥、口令、JWT、OA token、私钥禁止进入 Git、日志、错误、截图、URL 参数（厂商强制场景仅 adapter 内部且必须脱敏） |
| C2 | 凭据禁止进入 localStorage/sessionStorage；只用 HttpOnly Cookie |
| C3 | 禁止关闭审计、CSRF、权限中间件或提供审计删除 API |
| C4 | prod 禁止开发登录、默认密码、root SSH、共享 SSH 私钥 |
| C5 | 禁止把不可信文本当 prompt 指令；模型禁止拥有业务工具权限 |
| C6 | 禁止未经扫描的附件进入可下载区；一期禁止 ZIP/宏文档/可执行文件 |
| C7 | 禁止开放重定向、通配 CORS、宽泛 frame-ancestors、运行时外网 CDN/字体/遥测 |
| C8 | 禁止直接暴露 PG/Redis/Teable/API 端口到所有网卡 |

## D. 工程与交付

| # | 禁令 |
|---|---|
| D1 | 禁止 `latest`/浮动 tag；生产镜像必须有 commit SHA 和 digest |
| D2 | 禁止未审依赖、未提交 lockfile、未生成 SBOM 的制品进入部署 |
| D3 | 禁止跳过测试、伪造测试结果或把“未运行”写成“通过” |
| D4 | 禁止 API/schema/权限/阈值变更而不在同一 PR 同步文档和测试 |
| D5 | rules 禁止 IO、随机、直接读时钟；阈值禁止硬编码 |
| D6 | LLM 输出未经 JSON Schema + Pydantic 校验禁止落库/展示为结论 |
| D7 | 禁止 H5 hover-only、<44px 触控目标、横向滚动；禁止复制现有破损移动原型代码 |
| D8 | 禁止生产机 `git pull` 现场构建、直接编辑容器文件或手工改库 |
| D9 | 无 Pro 阶段仍禁止直推 main；本地 hook 可绕过不代表允许绕过 |
| D10 | 禁止删除/覆盖原始 DOCX、原型和审查证据，清场须在完整汇报后另行确认 |

## E. Agent 行为

- 需求/制度/安全责任不清时停止并提问。
- 代码不存在、测试未跑、服务未部署时必须写 pending/not-applicable。
- 先改权威文档，禁止创建“最终版2”“新版方案”等平行真相。
- 任务完成先洁癖对齐，再汇报；未获确认不得删除旧证据、分支、worktree 或临时库。

