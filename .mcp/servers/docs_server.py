"""
s20 docs MCP server：用真实 stdio MCP 暴露本地文档搜索工具。

函数职责：
- search：在当前 s20 工作区内搜索 README、Markdown 和 Python 文件。
"""

from __future__ import annotations

from pathlib import Path

from mcp.server import FastMCP


ROOT = Path(__file__).resolve().parents[2]
app = FastMCP("docs")


def _candidate_files() -> list[Path]:
    patterns = ["README.md", "**/*.md", "**/*.py"]
    files: list[Path] = []
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            if path.is_file() and "__pycache__" not in path.parts:
                if any(part.startswith(".") and part not in {".mcp"} for part in path.relative_to(ROOT).parts):
                    continue
                if path not in files:
                    files.append(path)
    return files


@app.tool()
def search(query: str, max_results: int = 5) -> str:
    """Search local project docs and source files for a query."""
    words = [word.lower() for word in query.split() if word.strip()]
    if not words:
        return "No query provided."
    matches = []
    for path in _candidate_files():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines, 1):
            haystack = line.lower()
            score = sum(1 for word in words if word in haystack)
            if score:
                rel = path.relative_to(ROOT)
                matches.append((score, str(rel), index, line.strip()))
    matches.sort(key=lambda item: (-item[0], item[1], item[2]))
    if not matches:
        return f"No matches for: {query}"
    output = []
    for _, rel, line_no, line in matches[: max(1, int(max_results))]:
        output.append(f"{rel}:{line_no}: {line[:300]}")
    return "\n".join(output)


if __name__ == "__main__":
    app.run(transport="stdio")
