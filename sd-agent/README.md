# sd-agent

FastAPI BFF 与独立 worker 使用同一 Python 包、两个入口：

```bash
uv sync --frozen
uv run uvicorn sd_agent.api_main:app --host 127.0.0.1 --port 8000
uv run python -m sd_agent.worker_main
```

生产配置从根目录 `.env.example` 派生，真实 `.env` 不进入 Git。当前实现状态以根 `docs/00-project-status.md` 为准。
