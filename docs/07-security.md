# 07 安全规范

## 1. 信任边界

```text
不可信：浏览器输入、OA redirect/ticket、Teable webhook、填报正文/附件、LLM 输出
受控区：Nginx、sd-api、sd-worker、Teable、PostgreSQL、Redis
外部依赖：OA、观远、GPU/模型服务、Harbor/Nexus、文件扫描服务
```

任何跨边界数据都要认证、授权、校验、限流、审计。内网不是信任理由。

## 2. SSO 与会话

- `/api/sso/oa/start` 生成随机 state/nonce，绑定目标相对路径与 5 分钟 HttpOnly Cookie；callback 必须恒定时间匹配，成功后删除 Cookie。
- redirect 仅允许配置中的站内相对路径，拒绝 scheme、host、`//`、反斜杠和双重编码。
- OA ticket 只使用一次，保存 hash，不保存明文；重放返回 401 并审计。
- JWT 仅含 `sub/sid/kid/iat/exp`；HttpOnly、Secure、SameSite=Lax。
- 状态写请求要求 `X-CSRF-Token`，并校验 Origin/Referer 属于精确 allowlist。
- `auth_session` 支持按 sid、用户、全部会话撤销；密钥用 kid 平滑轮换，不通过改全局密钥踢单人。
- prod `AUTH_DEV_LOGIN=true`、`SSO_MODE=stub` 或关键 secret 缺失时拒绝启动；stub 端点只在显式离线模式工作。

## 3. RBAC 与数据范围

| 操作 | domain_owner | unit_coordinator | supervision_admin | leader | ops_admin |
|---|---|---|---|---|---|
| task/log 读取 | 本人负责事项 | scope 内 | 全量 | 全量只读 | 仅运维元数据 |
| 填报 | 本人任务 | scope 内代报并标注 | 可代报并标注 | 否 | 否 |
| 回复/申诉 | 本人 | 否 | 裁决 | 否 | 否 |
| 领域台账维护 | 否 | measures | 全部 | 否 | 否 |
| 豁免/进度更正批准 | 否 | 否 | 是 | 否 | 否 |
| 追问确认/报告签发 | 否 | 否 | 是 | 只读 | 否 |
| 调度查看/重放 | 否 | 否 | 只读/审批 | 否 | 执行并审计 |

- 多角色取权限并集，但写操作必须满足该角色的范围条件；不能因同时拥有 leader 获得额外写权。
- 过滤在 Teable query/SQL 构造阶段完成，禁止取全量后内存过滤。
- API 返回的 `can.*` 与服务端 policy 使用同一纯函数；前端显隐不是安全边界。
- 角色/组织变更 5 分钟内生效；高风险撤权可立即 revoke session。

## 4. 审计

必审：登录成败、ticket 重放、403、角色/范围变化、填报、代报、进度更正、文件上传/下载、追问裁决、豁免、报告保存/签发、手动调度、dead-letter 重放、密钥/配置轮换、OA 外呼结果。

字段至少包括 `event_id/request_id/who/role/scope/what/target/result/ip/user_agent/created_at`。审计 API 不提供删除；DB 管理操作走单独运维账号并进入数据库审计。保留≥2年，导出需权限与留痕。

## 5. 密钥与服务器

- `.env` 600，不入 Git、镜像、备份明文、日志、截图和命令参数。
- GitHub、Harbor、OA、LLM 使用最小权限机器凭据；建立轮换和吊销清单。
- 服务器首次操作先轮换已通过对话传递的密码，安装 SSH 公钥，限制 `sdjg` sudo，随后关闭 SSH 密码认证（完成双通道验证后）。
- 禁止 root 远程登录、共享个人密钥、把私钥复制进项目。
- 内网 Harbor/Nexus 镜像和包必须签名/校验 digest；CI 生成 SBOM 和漏洞报告。

## 6. Web 安全

- 精确 CORS allowlist；无跨域需求时不返回 CORS。
- CSP 默认 `default-src 'self'`，禁止 CDN/远程字体/内联脚本；必要 nonce 由 Nginx/应用生成。
- `frame-ancestors` 只允许确认的 OA 门户；设置 HSTS、nosniff、严格 Referrer-Policy。
- Markdown 禁 HTML并经 DOMPurify；禁止 `v-html`/`innerHTML` 渲染业务文本。
- 登录/导出/文件/管理接口限流；错误不泄露堆栈、SQL、内部 URL、token。

## 7. 文件安全

- 一期允许 PDF、PNG、JPEG、DOCX、XLSX；ZIP/可执行文件/宏文档默认拒绝。
- 同时校验扩展名、声明 MIME 和 magic bytes；单文件≤20MB。
- prod 必须同步病毒扫描，扫描服务不可用时拒绝附件；隔离区不可被普通用户下载。
- 下载使用 BFF 对象级鉴权和 `Content-Disposition: attachment`；记录下载审计。
- Office/PDF 解析在隔离进程或专用服务，限制 CPU/内存/页数/解压大小，防解析器漏洞与炸弹。

## 8. LLM 安全

- 填报、附件和历史文本作为 `UNTRUSTED_DATA` 块，不与系统指令拼接。
- 不把 OA 账号、手机号、Cookie、密钥、无关个人信息送入模型。
- 模型无网络、数据库、OA、文件系统工具权限；输出只经过 schema/Pydantic 进入“建议”字段。
- 对提示注入、数据外泄请求、虚构来源、超长输入、Unicode 混淆做评测。
- `ai_run` 保存输入 hash、来源 ID、prompt/model/参数、输出、schema 结果和人工处置；不保存不必要的附件全文。

## 9. Webhook 与外呼

- Webhook 优先 HMAC(body+timestamp)，5 分钟重放窗；否则 secret+来源 IP+event_id durable 去重。
- OA token/password 位于 URL 的既有接口必须由 adapter 单独调用，关闭 URL/query logging、APM 捕获和代理缓存。
- 外呼错误落脱敏分类，不落完整响应正文；未知结果必须用同一业务幂等键重试。

## 10. 安全验收

至少覆盖：IDOR、跨 scope、角色叠加、CSRF、CORS、XSS、开放重定向、ticket/webhook 重放、session 撤销、JWT kid 轮换、暴力请求、恶意附件、提示注入、日志泄密、依赖/镜像漏洞、PG/Redis 端口暴露、备份恢复权限。详细用例见 docs/09。
