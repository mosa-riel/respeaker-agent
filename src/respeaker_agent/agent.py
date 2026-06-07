"""Agent loop — OpenAI-compatible /chat/completions with multi-round tool calling.

Flow (the shape the user specified):

    llm → tool (one OR MANY per turn) → action → result ─┐
     ↑                                                    │
     └──────────────── feed results back ─────────────────┘
    … repeats until the model returns a final answer → handed to TTS.

Each turn the model may emit several `tool_calls`. We run every one (the *action*),
collect the real *result*, append them as `role:"tool"` messages, and loop. We trust
the executed result, never the model's narration — the 'verify, don't trust' rule.
`tool_choice:"any"` can force a call on a must-act turn.

Every stage emits to the TraceBus: `llm-req`, `llm-rsp`, `tool` (action + result).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Secrets, Settings
from .tools import ToolRegistry
from .trace import TraceBus

_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)


@dataclass
class AgentResult:
    text: str
    rounds: int
    tool_calls: list[dict[str, Any]]  # {name, args, result} per executed call


class AgentLoop:
    def __init__(self, settings: Settings, secrets: Secrets, trace: TraceBus, tools: ToolRegistry) -> None:
        self._s = settings
        self._key = secrets.llm_api_key
        self._trace = trace
        self._tools = tools

    async def run(self, user_text: str, *, force_tool: bool = False, context: str | None = None) -> AgentResult:
        # `context` = live home overview (areas/devices/state) injected from the HA
        # MCP server per-conversation. Kept out of the static prompt so it never
        # goes stale. Phase 4 fills it in; None until then.
        system = self._s.system_prompt
        if context:
            system = f"{system}\n\nActuele context van dit huis:\n{context}"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
        specs = self._tools.specs()
        executed: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
            for round_i in range(1, self._s.max_tool_rounds + 1):
                msg = await self._chat(cli, messages, specs, force_tool and round_i == 1)
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    return AgentResult(text=(msg.get("content") or "").strip(), rounds=round_i, tool_calls=executed)

                # Keep the assistant turn (with its tool_calls) in the history.
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})

                # Execute EVERY requested call (there can be more than one).
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args = _parse_args(fn.get("arguments"))
                    self._trace.emit("tool", f"→ {name}({_compact(args)})", direction="out", data={"name": name, "args": args})
                    result = await self._tools.dispatch(name, args)
                    self._trace.emit("tool", f"← {name}: {_compact(result)}", direction="in", data={"name": name, "result": result})
                    executed.append({"name": name, "args": args, "result": result})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })

            # Ran out of rounds — one closing answer without tools.
            final = await self._chat(cli, messages, None, False)
            return AgentResult(text=(final.get("content") or "").strip(), rounds=self._s.max_tool_rounds, tool_calls=executed)

    async def _chat(self, cli: httpx.AsyncClient, messages: list[dict[str, Any]], specs: list[dict] | None, force: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self._s.llm_model, "messages": messages}
        if specs:
            body["tools"] = specs
            body["tool_choice"] = "any" if force else "auto"
        self._trace.emit("llm-req", _last_user(messages), direction="out",
                         data={"model": self._s.llm_model, "messages": messages, "tools": [s["function"]["name"] for s in (specs or [])]})
        url = f"{self._s.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        try:
            resp = await cli.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as err:
            self._trace.emit("error", f"LLM request failed: {_safe(err)}", level="error")
            raise
        msg = data["choices"][0]["message"]
        self._trace.emit("llm-rsp", (msg.get("content") or "").strip() or "(tool call)", direction="in",
                         data={"finish": data["choices"][0].get("finish_reason"), "tool_calls": msg.get("tool_calls")})
        return msg


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content", ""))[:200]
    return ""


def _compact(obj: Any) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str) if not isinstance(obj, str) else obj
    return s[:120]


def _safe(err: Exception) -> str:
    msg = str(err) or err.__class__.__name__
    return msg.splitlines()[0][:200]
