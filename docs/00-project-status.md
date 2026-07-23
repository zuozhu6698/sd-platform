# 00 项目状态与事实矩阵

更新日期：2026-07-23  
状态：`CORE_FOUNDATIONS_IN_PROGRESS`，工程基线已验证，会话鉴权端点、规则引擎、Teable adapter、幂等填报 saga 和 durable outbox 核心已实现；SSO 回调、OA 分发、文件上传/扫描、本人任务和外部 POC 未完成，不允许生产部署。

## 1. 已确认事实

| 主题 | 当前事实 | 状态 |
|---|---|---|
| GitHub owner | `zuozhu6698` | verified-current |
| 提交显示名 | `zuozhu6698` | verified-current |
| 仓库 | 个人私有仓库，计划名 `sd-platform` | verified-current |
| GitHub 方案 | 无 Pro，私有 main 暂不能强制保护 | verified-current |
| 应用服务器 | 已有 APP-SRV，承载 Nginx/Teable/PostgreSQL/Redis/API/worker | verified-current |
| GPU | 应用机不承担 32B 推理；GPU-SRV/统一模型接口尚未提供 | pending |
| 原始资料 | 上级 DOCX/HTML 作为背景与原型证据保留 | verified-current |
| 代码/测试/CI | FastAPI/Vue/Alembic/Compose/CI 文件已创建；本地门禁通过，远程 CI 未运行 | changed-and-verified-local |
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

## 3. 六个事实面

| 事实面 | 状态 | 证据/下一步 |
|---|---|---|
| 代码 | changed-and-verified | 后端 236 tests、90.26% 总覆盖率、纯规则模块 100%；前端 5 tests、被测模块 100% 覆盖率并完成 production build |
| 运行态 | not-applicable | 尚未部署，不写“可用” |
| 文档 | changed-and-verified | 本 docs 为现役工程契约 |
| 规则 | changed-and-verified | 根、后端、前端 AGENTS 分层 |
| 记忆 | out-of-scope | 不直接修改宿主生成记忆 |
| 工作区 | changed-and-verified | 独立本地 Git 仓库；`codex/g0-engineering-baseline`；remote/CI 仍 pending |

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
