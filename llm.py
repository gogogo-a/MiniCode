"""
LLM 调用模块：负责和 OpenAI Chat Completions 兼容接口通信，并处理临时错误。

函数职责：
- RecoveryState：记录当前模型、是否已经扩容、续写次数、压缩次数、连续 529 次数。
- retry_delay：计算 429/529 重试等待时间，支持 Retry-After。
- _status_and_body：从 HTTPError 中取状态码、响应体和 retry-after。
- is_prompt_too_long_error：判断异常是否属于上下文过长。
- _raw_chat：发起一次原始 /v1/chat/completions 请求。
- chat_with_system：带 429/529 指数退避和 fallback model 的 LLM 调用入口。
"""

import json
import random
import time
import http.client
import urllib.error
import urllib.request

from config import (
    API_KEY,
    BASE_DELAY_MS,
    BASE_URL,
    DEFAULT_MAX_TOKENS,
    FALLBACK_MODEL,
    MAX_CONSECUTIVE_529,
    MAX_RETRIES,
    PRIMARY_MODEL,
    TOKEN_FIELD,
)


class RecoveryState:
    def __init__(self):
        self.current_model = PRIMARY_MODEL
        self.has_escalated = False
        self.continuation_count = 0
        self.reactive_compact_count = 0
        self.consecutive_529 = 0


def retry_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    base = min(BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    return base + random.uniform(0, base * 0.25)


def _status_and_body(error: Exception) -> tuple[int | None, str, str | None]:
    status = getattr(error, "code", None)
    retry_after = None
    body = str(error)
    if isinstance(error, urllib.error.HTTPError):
        status = error.code
        retry_after = error.headers.get("retry-after")
        try:
            body = error.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(error)
    return status, body, retry_after


def is_prompt_too_long_error(error: Exception) -> bool:
    status, body, _ = _status_and_body(error)
    text = f"{status or ''} {body}".lower()
    return (
        "prompt_too_long" in text
        or "prompt_is_too_long" in text
        or "context_length_exceeded" in text
        or "too many tokens" in text
        or "maximum context" in text
        or "max_context_window" in text
    )


def _raw_chat(messages: list, tools: list, system: str, model: str, max_tokens: int) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    payload[TOKEN_FIELD] = max_tokens
    request = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def chat_with_system(messages: list, tools: list, system: str, state: RecoveryState, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            response = _raw_chat(messages, tools, system, state.current_model, max_tokens)
            state.consecutive_529 = 0
            return response
        except Exception as error:
            status, body, retry_after = _status_and_body(error)
            text = body.lower()
            if status == 429 or "429" in text or "rate limit" in text or "ratelimit" in text:
                delay = retry_delay(attempt, retry_after)
                print(f"\033[33m[429 rate limit] retry {attempt + 1}/{MAX_RETRIES}, wait {delay:.1f}s\033[0m")
                time.sleep(delay)
                continue
            if isinstance(error, (http.client.RemoteDisconnected, urllib.error.URLError, TimeoutError, ConnectionError)):
                delay = retry_delay(attempt, retry_after)
                print(f"\033[33m[transient connection] retry {attempt + 1}/{MAX_RETRIES}, wait {delay:.1f}s\033[0m")
                time.sleep(delay)
                continue
            if status == 529 or "529" in text or "overloaded" in text:
                state.consecutive_529 += 1
                if state.consecutive_529 >= MAX_CONSECUTIVE_529:
                    if FALLBACK_MODEL:
                        state.current_model = FALLBACK_MODEL
                        print(f"\033[31m[529 x{MAX_CONSECUTIVE_529}] switching to {FALLBACK_MODEL}\033[0m")
                    else:
                        print(f"\033[31m[529 x{MAX_CONSECUTIVE_529}] no fallback model configured\033[0m")
                    state.consecutive_529 = 0
                delay = retry_delay(attempt, retry_after)
                print(f"\033[33m[529 overloaded] retry {attempt + 1}/{MAX_RETRIES}, wait {delay:.1f}s\033[0m")
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"Max retries ({MAX_RETRIES}) exceeded")
