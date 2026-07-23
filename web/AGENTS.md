# AGENTS.md — web

先读根 `AGENTS.md`、`docs/04-api-contracts.md`、`docs/07-security.md`、`docs/08-prohibitions.md`。

## 目录

```text
web/
├── package.json / pnpm-lock.yaml
├── src/
│   ├── api/ auth/ router/ stores/
│   ├── pages/ components/
│   ├── features/{report,map,review,weekly,ledger}/
│   └── styles/design-tokens.css
├── tests/
└── e2e/
```

## 边界

- 一切请求走 `src/api/` 唯一 axios 实例；组件禁止直接 fetch/axios。
- Cookie 由浏览器处理，前端禁止读取/保存 JWT；CSRF token 仅内存保存。
- `can.*` 只控制 UI，真正权限由 BFF 决定；403 必须正常处理。
- 禁止 Teable/PG/vLLM/OA 地址、运行时 CDN、远程字体和外网遥测。
- 禁止 `v-html`/`innerHTML` 渲染业务文本；Markdown 禁 HTML并净化。

## 体验

- 每页实现 empty/loading/error/403/503/degraded 五类状态和恢复入口。
- H5 375px 无横向滚动，触控目标≥44px；PC 支持键盘、可见焦点、200% 缩放。
- 地图 PC 专用，H5 使用列表替代；不要复制原型中的固定侧栏和内联 onclick。
- 双击/重复提交由前端禁用按钮改善体验，但仍依赖服务端 Idempotency-Key 保证正确性。

## 技术基线

Node 24 LTS、pnpm 10、Vue 3.5、Vite 8.1、TypeScript strict。颜色只用 design tokens；新增状态语义先改 docs/01 与 token 文件。

## 测试

Vitest 覆盖 API/stores/组件状态，Playwright 覆盖 SSO stub、权限、填报并发、慢网、过期会话、PC/H5 和可访问性。测试断言用户可见结果，不只断言“能渲染”。

