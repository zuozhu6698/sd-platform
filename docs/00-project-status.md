# 00 项目状态与事实矩阵

更新日期：2026-07-23  
状态：`CORE_FOUNDATIONS_IN_PROGRESS`，工程基线已验证，会话鉴权端点、本人任务、规则引擎、Teable adapter、幂等填报 saga 和 durable outbox 核心已实现；SSO 回调、OA 分发、文件上传/扫描和外部 POC 未完成，不允许生产部署。

## 1. 已确认事实

| 主题 | 当前事实 | 状态 |
|---|---|---|
| GitHub owner/remote | `zuozhu6698` / `https://github.com/zuozhu6698/sd-platform.git` | verified-current |
| 提交显示名 | `zuozhu6698` | verified-current |
| 仓库 | 个人 public 仓库 `zuozhu6698/sd-platform` | verified-current |
| GitHub 方案 | GitHub Free public 仓库；首次引导后启用 main 保护与 required checks | changed-not-yet-verified |
| 应用服务器 | 已完成只读资产核验；详细清单不进入 public 仓库 | verified-current-local |
| 服务器运行态 | 现状不满足目标 Compose 栈的直接部署前提，需运维完成容量与运行时决策 | pending |
| GPU | 应用机不承担 32B 推理；GPU-SRV/统一模型接口尚未提供 | pending |
| 原始资料 | 上级 DOCX/HTML 作为背景与原型证据保留 | verified-current |
| 代码/测试/CI | FastAPI/Vue/Alembic/Compose/CI 已创建；本地门禁通过；GitHub Actions run `29989304790` 四个 job 全绿 | changed-and-verified-remote |
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

## 3. 六个事实面

| 事实面 | 状态 | 证据/下一步 |
|---|---|---|
| 代码 | changed-and-verified | 后端 262 tests、91.21% 总覆盖率、纯规则模块 100%；前端 9 tests、被测模块 100% 覆盖率并完成 production build |
| 运行态 | not-applicable | 尚未部署，不写“可用” |
| 文档 | changed-and-verified | 本 docs 为现役工程契约 |
| 规则 | changed-and-verified | 根、后端、前端 AGENTS 分层 |
| 记忆 | out-of-scope | 不直接修改宿主生成记忆 |
| 工作区 | changed-and-verified | 独立 Git 仓库；功能分支与 main 已推送；remote CI 全绿；GitHub 默认分支/保护规则仍待设置 |

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
