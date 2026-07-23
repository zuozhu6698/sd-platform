# 10 Git、PR、CI 与发布

## 1. 仓库事实

- GitHub owner：`zuozhu6698`。
- 目标：个人私有仓库 `zuozhu6698/sd-platform`。
- 当前无 GitHub Pro，私有仓库不能强制启用 protected branch。PR/CI 仍执行，但平台无法阻止 owner 直推/强行合并。
- 提交显示名：`zuozhu6698`。提交邮箱在第一次 commit 前选择直接邮箱或 GitHub noreply；不要在仓库文件里硬编码个人邮箱。

## 2. 首次建仓步骤

以下命令由 G0 执行，执行前先 secret scan，不把上级 `D:\Project` 大仓库当项目仓库。

```powershell
Set-Location <sd-platform-repo-root>
git init -b main
git config user.name "zuozhu6698"
git config user.email "<confirmed-email>"
git add .
git update-index --chmod=+x .githooks/pre-push
git config core.hooksPath .githooks
git status
git diff --cached
git commit -m "chore(G0): 建立工程与文档基线"

gh auth login
gh repo create zuozhu6698/sd-platform --private --source . --remote origin
$env:SD_ALLOW_INITIAL_MAIN_PUSH = "initial-bootstrap"
git push -u origin main
Remove-Item Env:SD_ALLOW_INITIAL_MAIN_PUSH
```

首次 push 前必须检查：`git diff --cached`、secret scan、`git status`、remote URL。首次发布 `main` 是唯一例外，随后启用版本化钩子；不得把服务器密码、真实 `.env`、证书、备份、上传、模型权重、数据库、日志或原始敏感样本加入 Git。

## 3. 分支与提交

- `main`：始终可部署，只接受 PR squash merge。
- 任务分支：`codex/<card-id>-<short-slug>`，如 `codex/B4-secure-report`。
- 紧急修复：`codex/hotfix-<issue>`，同样走 PR/CI。
- 一个分支只解决一个任务卡；跨卡依赖拆成多个 PR。

提交格式：`type(scope): 中文动作`，scope 使用任务卡 ID。

```text
feat(B4): 增加填报幂等命令
test(B6): 覆盖 outbox worker 崩溃恢复
docs(G1): 同步 SSO 重定向契约
fix(F2): 修复 H5 重复提交提示
```

## 4. 无 Pro 阶段补偿控制

1. 提供版本化 `.githooks/pre-push`，默认拒绝从 main 直接 push；G0 设置 `core.hooksPath`。
2. README/AGENTS 明文禁止直推；每次任务从 `codex/*` 分支开始。
3. PR 模板要求任务卡、风险、测试命令/结果、文档影响、截图/trace、回滚。
4. GitHub Actions 全绿后才人工 squash merge。
5. 每周审计 main 是否存在非 PR commit；发现一次即记录并补流程。

本地 hook 可被绕过，只是防误操作。仓库进入生产或多人协作前，升级 GitHub Pro 或迁入具备规则保护的组织仓库。

平台能力依据：[GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)。建仓时再次核对账号当日套餐能力；若 GitHub 已为该私有仓库开放等效 ruleset，应优先启用平台强制规则。

## 5. CI 必需 job

| Job | 内容 |
|---|---|
| docs | Markdown/链接/引用/secret/契约一致性 |
| backend | uv frozen、ruff、typecheck、pytest+coverage |
| frontend | pnpm frozen、lint、typecheck、Vitest、build |
| integration | testcontainers、adapter contracts、migration |
| e2e | TEST 依赖 + Playwright 关键旅程 |
| security | secret、依赖、许可证、SBOM、容器/IaC 扫描 |
| package | 构建 sd-agent/web 镜像，按 commit SHA 标记并输出 digest |

Actions 引用第三方 action 必须固定完整 commit SHA。任何 job 名在 workflows 中保持唯一，避免未来 branch protection 状态歧义。

## 6. 制品与环境推进

```text
PR commit
  └─ CI test/security
      └─ merge main
          └─ build image:<git-sha> + digest + SBOM
              └─ sync Harbor TEST
                  └─ TEST deploy + smoke + migration evidence
                      └─ approve
                          └─ PROD deploy same digest
```

TEST 与 PROD 使用同一 digest，不重新构建。配置通过受控环境文件/密钥系统注入，不能从 Git 分支派生生产秘密。

## 7. 发布与回滚

- 试点前使用 `v0.x.y`；正式生产从 `v1.0.0` 开始。
- release marker 包含 Git SHA、镜像 digest、Alembic revision、Teable release/digest、配置 hash、部署时间。
- 应用回滚到上一个已验证 digest；数据库迁移先判断向后兼容。
- 采用 expand/contract：先加字段/表和兼容代码，再切 consumer，最后单独版本删除。
- 不可逆迁移必须在 PR 和 runbook 标红，并有恢复备份和人工批准。

## 8. 每次开发循环

```text
同步 main → 新建 codex 分支 → 读取任务卡/契约 → 测试先行 → 实现
→ 全门禁 → 洁癖同步文档 → PR → CI → squash merge → 制品 → TEST → 发布
```

禁止在服务器上修改源文件后反向拷回 Git。生产差异必须从仓库和制品重建。
