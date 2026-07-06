"""
配置模块：集中保存 s15 运行时会用到的路径、模型、接口、恢复参数、定时任务目录和团队邮箱目录。

主要内容：
- WORKDIR / MEMORY_DIR / SKILLS_DIR：定义当前 agent 的工作区和资源目录。
- SCHEDULED_TASKS_DIR / SCHEDULED_LOCKS_DIR / SCHEDULED_LOGS_DIR：保存系统级定时任务、锁和日志。
- SCHEDULED_MODE：标记当前是否由系统定时 runner 执行。
- MAILBOX_DIR：保存 Lead 和队友的 jsonl 收件箱。
- BASE_URL / API_KEY / PRIMARY_MODEL：读取 OpenAI 兼容接口配置。
- FALLBACK_MODEL / TOKEN_FIELD：控制 529 后切换模型，以及 token 限制字段名。
- DEFAULT_MAX_TOKENS / ESCALATED_MAX_TOKENS：控制截断后的输出额度升级。
- MAX_CONTINUATIONS / MAX_REACTIVE_COMPACTS / MAX_RETRIES：控制恢复次数上限。
- CONTEXT_LIMIT / MAX_MESSAGES / TOOL_RESULT_BUDGET：控制上下文压缩阈值。
- CONTINUATION_PROMPT：输出被截断后让模型继续写的提示词。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

WORKDIR = Path.cwd()
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
SCHEDULED_TASKS_DIR = WORKDIR / ".scheduled_tasks"
SCHEDULED_LOCKS_DIR = WORKDIR / ".scheduled_locks"
SCHEDULED_LOGS_DIR = WORKDIR / ".logs" / "scheduled"
SCHEDULED_MODE = os.getenv("SCHEDULED_MODE") == "1"
MAILBOX_DIR = WORKDIR / ".mailboxes"

BASE_URL = os.environ["OPENAI_BASE_URL"].rstrip("/")
API_KEY = os.environ["OPENAI_API_KEY"]
PRIMARY_MODEL = os.environ["OPENAI_MODEL"]
FALLBACK_MODEL = os.getenv("OPENAI_FALLBACK_MODEL") or os.getenv("FALLBACK_MODEL_ID")
TOKEN_FIELD = os.getenv("OPENAI_TOKEN_FIELD", "max_tokens")

DEFAULT_MAX_TOKENS = int(os.getenv("S11_DEFAULT_MAX_TOKENS", "8000"))
ESCALATED_MAX_TOKENS = int(os.getenv("S11_ESCALATED_MAX_TOKENS", "64000"))
MAX_CONTINUATIONS = int(os.getenv("S11_MAX_CONTINUATIONS", "3"))
MAX_REACTIVE_COMPACTS = int(os.getenv("S11_MAX_REACTIVE_COMPACTS", "3"))
MAX_RETRIES = int(os.getenv("S11_MAX_RETRIES", "10"))
BASE_DELAY_MS = 500
MAX_CONSECUTIVE_529 = 3

CONTEXT_LIMIT = 12000
MAX_MESSAGES = 50
KEEP_RECENT_TOOL_RESULTS = 3
PERSIST_THRESHOLD = 4000
TOOL_RESULT_BUDGET = 10000
CONSOLIDATE_THRESHOLD = 10

CONTINUATION_PROMPT = (
    "Output token limit hit. Resume directly. "
    "Do not apologize or recap. Continue exactly where you stopped."
)
