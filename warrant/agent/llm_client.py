"""
agent/llm_client.py — Client for the local LLM (vLLM, OpenAI-compatible API).

Sends the conversation plus tool definitions and returns the assistant message,
with tool calls normalized to [{"id", "name", "arguments": dict}].

It never executes anything. Executing tools is agent.py's job.

Tool calls are read from the structured `tool_calls` field. If the server was
started without a tool-call parser, Qwen/Hermes-style <tool_call>{...}</tool_call>
blocks in the text content are accepted as a fallback.
"""

import json
import re
import urllib.request

from warrant.agent import config

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url=config.LLM_BASE_URL, model=config.LLM_MODEL,
                 timeout=config.LLM_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list, tools: list) -> dict:
        """Returns {"content": str, "tool_calls": [...], "raw": assistant message}."""
        body = {
            "model": self.model or self._default_model(),
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": config.LLM_TEMPERATURE,
            "max_tokens": config.LLM_MAX_TOKENS,
        }
        response = self._request("POST", "/chat/completions", body)
        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"unexpected response: {str(response)[:300]}")

        content = _THINK_RE.sub("", message.get("content") or "").strip()
        calls = [
            {
                "id": c.get("id") or f"call_{i}",
                "name": c["function"]["name"],
                "arguments": _parse_args(c["function"].get("arguments")),
            }
            for i, c in enumerate(message.get("tool_calls") or [])
        ]
        if not calls:
            for i, block in enumerate(_TOOL_CALL_RE.findall(content)):
                try:
                    parsed = json.loads(block)
                    calls.append({"id": f"call_{i}", "name": parsed["name"],
                                  "arguments": _parse_args(parsed.get("arguments"))})
                except (ValueError, KeyError):
                    continue
            content = _TOOL_CALL_RE.sub("", content).strip()
        return {"content": content, "tool_calls": calls, "raw": message}

    def _default_model(self) -> str:
        models = self._request("GET", "/models").get("data") or []
        if not models:
            raise LLMError("LLM server reports no models")
        self.model = models[0]["id"]
        return self.model

    def _request(self, method: str, path: str, body: dict = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            raise LLMError(f"{method} {path} failed: {exc!r}") from exc


def _parse_args(arguments) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}
