# 本地 Git 钩子

本目录提供个人私有仓库在无 GitHub Pro 分支保护时的补偿控制。

启用：

```bash
git config core.hooksPath .githooks
git update-index --chmod=+x .githooks/pre-push
```

`pre-push` 默认拒绝直接推送 `main`。仅首次创建远端 `main` 时，可显式运行：

```bash
SD_ALLOW_INITIAL_MAIN_PUSH=initial-bootstrap git push -u origin main
```

本地钩子可以被绕过，因此它必须与 Pull Request、CI 门禁和发布审计同时使用。
