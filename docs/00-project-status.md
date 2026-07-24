# 00 项目状态与事实矩阵

更新日期：2026-07-24
状态：`WAVE_1_IN_PROGRESS`，工程基线、会话鉴权端点、本人任务、规则引擎、Teable adapter、幂等填报 saga、安全附件链路、durable outbox、dead letter 双人审批、计划任务唯一运行框架和 OA `complete_pending` mock/adapter 已远程验证；SSO stub 回调与登录审计已在 B3 分支本地验证，真实 OA/SSO 保持 `pending-EXT-03`；业务 handlers、自动对账和 J0 未完成，不允许进入部署。

## 1. 已确认事实

| 主题 | 当前事实 | 状态 |
|---|---|---|
| GitHub owner/remote | `zuozhu6698` / `https://github.com/zuozhu6698/sd-platform.git` | verified-current |
| 提交显示名 | `zuozhu6698` | verified-current |
| 仓库 | 个人 public 仓库 `zuozhu6698/sd-platform` | verified-current |
| GitHub 方案 | `main` 为默认且受保护；PR、严格 required checks、管理员约束、线性历史、禁 force push/删除 | changed-and-verified-remote |
| 应用服务器 | 已完成只读资产核验；详细清单不进入 public 仓库 | verified-current-local |
| 服务器运行态 | 现状不满足目标 Compose 栈的直接部署前提，需运维完成容量与运行时决策 | pending |
| GPU | 应用机不承担 32B 推理；GPU-SRV/统一模型接口尚未提供 | pending |
| 原始资料 | 上级 DOCX/HTML 作为背景与原型证据保留 | verified-current |
| 代码/测试/CI | FastAPI/Vue/Alembic/Compose/CI 已创建；PR #10 已经受保护主干合并，合并后 GitHub Actions run `30064150026` 四个 job 全绿；B3 SSO stub 在分支完成本地门禁，待独立 PR | changed-and-verified |
| 部署/live | 未部署 | not-applicable |

服务器地址、登录账户、口令、真实域名、OA 密钥不进入 Git 文档。口令曾通过对话传递，首次服务器操作前必须轮换并改用 SSH 密钥。

## 2. 外部阻塞项

| ID | 待确认 | 责任方 | 阻塞 Gate |
|---|---|---|---|
| EXT-01 | GPU-SRV 或集团模型服务地址、认证、模型和 SLA | 信息部/AI 平台 | P2、M3 |
| EXT-02 | 集团内网 DNS 子域和内部 CA 证书 | 网络/运维 | M4 |
| EXT-03 | OA 测试 rest 账号、registerCode、SSO 配置和测试组织账号 | OA 管理员 | P1、M2 |
| EXT-04 | 观远测试环境来源 IP、账号和网络策略 | BI 管理员 | M4 |
| EXT-05 | Harbor/Nexus 地址、镜像同步和制品保留策略 | 运维 | G0、M4 |
| EXT-06 | 需求、架构、安全、上线签字人 | 项目负责人 | Gate 0、M5 |
| EXT-07 | APP-SRV 容量/拆分方案与容器运行时安装授权 | 运维/项目负责人 | O1、M4 |

EXT-01/03/04/05/07 当前均不阻塞 Wave 1/2：按 `docs/12 §4b` 使用受控本地替身完成应用闭环；但对应真实 POC、TEST、部署和上线状态必须保持 `pending-EXT-*`，不得用替身证据替代。

## 3. 六个事实面

| 事实面 | 状态 | 证据/下一步 |
|---|---|---|
| 代码 | changed-and-verified | B3 分支后端 411 tests、95.27% 总覆盖率、SSO/outbox/scheduler/OA 核心模块 100%；前端 11 tests、被测模块 100% 覆盖率并完成 production build |
| 运行态 | not-applicable | 尚未部署，不写“可用” |
| 文档 | changed-and-verified | 本 docs 为现役工程契约 |
| 规则 | changed-and-verified | 根、后端、前端 AGENTS 分层 |
| 记忆 | out-of-scope | 不直接修改宿主生成记忆 |
| 工作区 | changed-and-verified | PR #10 四项 CI 全绿后 squash merge；`main`=`4ce4de7` 且受保护；B3 开发位于 `codex/b3-sso-stub` |

## 4. Gate 定义

```text
Gate 0  文档签字 + 外部责任人确认
Gate 1  Git/CI/密钥扫描/工程骨架完成
Gate 2  Teable/OA/GPU 三项 POC 有报告
Gate 3  最小业务闭环通过自动测试
Gate 4  TEST 部署、恢复/降级/安全/性能验收
Gate 5  试点双轨两周、人工签字
Gate 6  生产发布与 live verification
```

任何 Gate 未通过，后续阶段只能做不会固化错误假设的准备工作。
