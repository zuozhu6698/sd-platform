# 13 决策与开放项

## 1. 已定工程决策

| ID | 决策 | 原因 |
|---|---|---|
| ADR-001 | 正式填报使用自研 PC/H5 + BFF，禁 Teable 共享表单 | 身份、范围、幂等、附件和审计必须在应用层 |
| ADR-002 | Teable 9 张领域表；PostgreSQL `sd_app` 工程表；`bi` 稳定合同 | 消除“6表却依赖未建表”的矛盾 |
| ADR-003 | `person.role` 改为 role_assignment，多角色/范围建模 | 真实组织中一人可兼任多角色 |
| ADR-004 | primary owner 使用 task_owner 明确字段，不依赖多选顺序 | 顺序语义脆弱且难验证 |
| ADR-005 | sd-api/sd-worker 同镜像分进程；PG durable outbox | 单机足够、批任务不拖 API、可恢复而不过度引入 MQ |
| ADR-006 | Teable/app 使用两个 Redis；Redis 非正确性来源 | 隔离故障域并避免缓存丢失破坏流程 |
| ADR-007 | JWT 只含 sub/sid/kid；权限缓存 5 分钟并可失效 | 角色变化不等待 8 小时，支持单会话撤销 |
| ADR-008 | 进度回退走追加 correction，不再绝对禁止 | 真实计划可修正，同时保留审计 |
| ADR-009 | vLLM 使用新版 structured outputs/response_format | 旧 guided API 已移除 |
| ADR-010 | 生产只部署不可变制品，禁服务器 git pull 现场构建 | 可追溯、可回滚、环境一致 |
| ADR-011 | Node 24 LTS + Vite 8.1；不使用 Node 20/Vite 5 新建项目 | 2026 支持与安全基线 |
| ADR-012 | 内网域名不用 `.local`，等待企业 DNS 子域 | 避免 mDNS/证书信任冲突 |

这些是工程基线决策。若业务/安全/运维签字人否决，必须新增 superseding ADR 并同步所有受影响分册，不能直接改一处文字。

## 2. 开放项

| ID | 问题 | 推荐 | 责任方 | 最晚 Gate |
|---|---|---|---|---|
| OPEN-01 | Git commit 使用直接邮箱还是 noreply | 私有仓库可直接邮箱；重视隐私则 noreply | repo owner | G0 首 commit |
| OPEN-02 | GPU-SRV/统一模型接口 | 优先集团统一服务；无则独立 2×24G/48G POC | AI 平台 | P2 |
| OPEN-03 | 实际内网域名/证书 | 企业持有域名子域 + 内部 CA | 网络/运维 | O1 |
| OPEN-04 | OA token 接口是否有更安全版本 | 优先 header/body 新接口；否则 URL 日志全链脱敏 | OA 管理员 | P1 |
| OPEN-05 | 文件扫描服务 | 优先企业现有 AV API；否则评估 ClamAV 资源 | 安全/运维 | B4 |
| OPEN-06 | Teable AGPL 义务 | 法务/开源合规评估修改和网络使用义务 | 法务/信息部 | P0 |
| OPEN-07 | 观远来源 IP/字段命名 | 确认 allowlist 与双语字段合同 | BI 管理员 | J2 |
| OPEN-08 | 审计/附件/AI 保留期 | 制度签字后实现自动清理 | 业务/法务/安全 | Gate 4 |
| OPEN-09 | 业务、安全、运维签字人 | 每类至少一人 | 项目负责人 | Gate 0 |

## 3. 决策记录规则

- 开放项关闭时记录日期、决定人、证据链接和影响文件。
- 任何“暂定”必须有 owner 和最晚 Gate；没有 owner 的暂定不进入实现。
- 技术实现可以由 Codex完成，业务口径、权限、合规和上线接受风险不能由 Agent 代签。

