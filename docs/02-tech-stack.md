# 02 技术栈与版本策略

## 1. 版本原则

1. 文档锁支持线，lockfile 锁精确依赖，容器按 digest 锁不可变制品。
2. `latest`、`stable`、浮动 major tag 不得进入生产 Compose。
3. 每次升级先在 TEST 跑迁移、回滚、接口契约、性能和恢复测试，再更新本表与制品 digest。
4. 依赖版本以仓库 lockfile/镜像清单为运行事实；本文不冒充尚未生成的 lockfile。

## 2. 后端

| 项 | 基线 | 约束 |
|---|---|---|
| Python | 3.11.x | 容器锁 patch + digest；安全支持期内使用 |
| 包管理 | uv | `pyproject.toml` + `uv.lock` 必须提交 |
| API | FastAPI + Pydantic v2 | 精确版本由 G0 生成并经 lockfile 固化 |
| DB | SQLAlchemy Core 2.x + asyncpg + Alembic | 可写仅 `sd_app`/`bi` 自有 schema；Teable 领域写走 REST |
| 调度 | APScheduler 3.11.x | 仅 sd-worker；PostgreSQL advisory lock/唯一键兜底 |
| HTTP | httpx | OA/Teable/LLM 统一超时、重试和脱敏日志 |
| 缓存 | redis-py | 仅缓存/加速；业务正确性不依赖 Redis |
| JWT | PyJWT 2.x | HS256 多 kid；会话撤销在 `auth_session` |
| 日志 | structlog | JSON、request_id、trace_id，敏感字段 denylist |
| 测试 | pytest、pytest-asyncio、respx、testcontainers | 测试不真实外呼 |
| 质量 | ruff、mypy/pyright（二选一在 G0 定案） | CI 阻断 |

后端 API 与 worker 使用同一镜像、两个启动命令，避免重复领域代码，同时隔离请求延迟与批任务。

## 3. 前端

| 项 | 基线 | 约束 |
|---|---|---|
| Node | 24.18.x LTS | Node 20 已不作为新项目基线；G0 锁当日最新安全 patch |
| 包管理 | pnpm 10.x | `pnpm-lock.yaml` 提交，CI frozen lockfile |
| 框架 | Vue 3.5 + `<script setup>` | TypeScript strict |
| 构建 | Vite 8.1.5 supported line | 不使用已停止支持的 Vite 5；升级锁当前 minor |
| 状态 | Pinia | 只存 UI/会话展示状态，不存凭据/业务副本 |
| 组件 | Ant Design Vue 4 | 先用组件库能力，禁止平行造轮子 |
| 地图 | AntV X6 2.x + dagre/Worker | 版本经 POC 锁定 |
| 请求 | axios 单实例 | `src/api` 唯一出口，CSRF/header/request-id 统一处理 |
| 富文本 | markdown-it + DOMPurify | 默认禁 HTML；渲染后再净化 |
| 测试 | Vitest、Vue Test Utils、Playwright | PC/H5/键盘/错误态均覆盖 |

截至 2026-07-23，Node 24 为 LTS，Vite 8.1 是常规修复线。升级依据只使用官方发布页并在 PR 记录。

## 4. 基础设施

| 项 | 基线 | 约束 |
|---|---|---|
| Teable CE | POC 以 2026-05-28 官方 release 为候选 | AGPL 评估；精确镜像 digest 由 POC 固化 |
| PostgreSQL | 15.17+（15 线） | 锁当日安全 patch + digest；Teable schema 与 `sd_app`/`bi` 权限隔离 |
| Redis | 7.4.8+（7.4 线）两实例 | POC 验证 Teable 兼容性；Teable/app 分离到独立缓存网络 |
| Nginx | 1.30.4 stable line | TLS、安全头、限流、日志脱敏 |
| vLLM | 0.22.1 POC 候选 | GPU-SRV 独立；按显卡/驱动/模型兼容性锁最终版本与 digest |
| 模型 | Qwen3-32B AWQ 为候选，不是既定事实 | 由 P2 的质量/显存/延迟报告定案 |
| 编排 | Docker Engine + Compose v2 | 单 APP-SRV；不用 Kubernetes |
| 制品 | GitHub Actions → 内网 Harbor/Nexus | 生产按 commit SHA/digest 部署 |

vLLM 新版使用 `response_format=json_schema` 或 `structured_outputs`。已移除的 `guided_json`、`guided_decoding_backend` 及旧 `--guided-decoding-backend outlines` 不得进入实现。

版本事实核对入口（审查日 2026-07-23）：

- [Node.js 24.18.0 LTS](https://nodejs.org/en/blog/release/v24.18.0)
- [Vite supported versions](https://vite.dev/releases.html)
- [Teable releases](https://github.com/teableio/teable/releases) 与 [Docker 部署](https://help.teable.io/en/deploy/docker)
- [PostgreSQL 15.17](https://www.postgresql.org/docs/release/15.17/)
- [Nginx 下载与 stable line](https://nginx.org/en/download.html)
- [vLLM releases](https://github.com/vllm-project/vllm/releases) 与 [structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/)

G0 把核对日期、选定 patch、镜像 digest 与许可证结果写入制品清单。

## 5. 依赖准入

新增依赖必须在同一 PR 给出：用途、内置能力为何不足、候选对比、许可证、维护状态、包体/资源成本、漏洞扫描结果、回退方式。以下情况直接拒绝：

- 运行时外网 CDN、遥测或远程字体；
- 与现有 HTTP/状态/UI 库功能重复；
- 无明确许可证或停止维护；
- 为一个简单任务引入消息集群、服务网格或 Kubernetes；
- 无 lockfile、无 SBOM、无法进入内网制品库。

## 6. 外部系统适配

OA、观远、Teable、LLM 均通过 adapter 接口封装。业务 service 不能拼接外部 URL。外部接口版本差异落在配置和 adapter contract test，不散落条件分支。
