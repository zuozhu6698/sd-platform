from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {
    ".git",
    ".tooling",
    ".venv",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "playwright-report",
    "test-results",
}
REQUIRED_DOCS = [
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    *(
        ROOT / "docs" / f"{index:02d}-{name}.md"
        for index, name in [
            (0, "project-status"),
            (1, "architecture"),
            (2, "tech-stack"),
            (3, "database"),
            (4, "api-contracts"),
            (5, "modules"),
            (6, "middleware"),
            (7, "security"),
            (8, "prohibitions"),
            (9, "testing"),
            (10, "git-delivery"),
            (11, "deployment-runbook"),
            (12, "implementation-plan"),
            (13, "decisions-and-open-items"),
        ]
    ),
]
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
SECRET_PATTERNS = {
    "private key": re.compile(r"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
}


def iter_text_files() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in {
            ".md",
            ".yml",
            ".yaml",
            ".toml",
            ".json",
            ".ts",
            ".vue",
            ".py",
            ".example",
        }:
            result.append(path)
    return result


def check_required() -> list[str]:
    return [
        f"缺少权威文件：{path.relative_to(ROOT)}" for path in REQUIRED_DOCS if not path.is_file()
    ]


def check_links() -> list[str]:
    errors: list[str] = []
    for document in ROOT.rglob("*.md"):
        if any(part in SKIP_PARTS for part in document.parts):
            continue
        content = document.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(content):
            target = raw_target.strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_text = target.split("#", 1)[0]
            if not path_text or path_text.startswith("<"):
                continue
            resolved = (document.parent / path_text).resolve()
            if not resolved.exists():
                errors.append(f"失效链接：{document.relative_to(ROOT)} -> {target}")
    return errors


def check_secrets() -> list[str]:
    errors: list[str] = []
    for path in iter_text_files():
        content = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                errors.append(f"疑似 {label}：{path.relative_to(ROOT)}")
    return errors


def main() -> int:
    errors = [*check_required(), *check_links(), *check_secrets()]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("DOCS_GATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
