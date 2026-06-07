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

import asyncio
import json
import time
from dataclasses import dataclass, field
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
    messages: list[dict[str, Any]] = field(default_factory=list)  # history to persist (no system)


class AgentLoop:
    def __init__(self, settings: Settings, secrets: Secrets, trace: TraceBus, tools: ToolRegistry) -> None:
        self._s = settings
        self._key = secrets.llm_api_key
        self._trace = trace
        self._tools = tools

    async def run(
        self,
        user_text: str,
        *,
        history: list[dict[str, Any]] | None = None,
        force_tool: bool = False,
        context: str | None = None,
    ) -> AgentResult:
        # `history` = prior turns (user/assistant/tool messages, NO system) so
        # follow-ups like "ja" / "doe maar" keep context. `context` = live home
        # overview injected fresh each turn so it never goes stale (phase 4).
        system = self._s.system_prompt
        if context:
            system = (
                f"{system}\n\n=== TOOL-BRONNEN ===\n{context}\n"
                "Kies de juiste tool per vraag. Je kent de apparaten NIET uit je hoofd: "
                "gebruik de zoek-/lijst-tool van home-assistant om het juiste apparaat "
                "+ entity_id te vinden, en de staat-tool voor de actuele staat. Verzin "
                "nooit een apparaat, entity_id of resultaat."
            )
        convo: list[dict[str, Any]] = list(history or [])  # everything after system
        convo.append({"role": "user", "content": user_text})
        specs = self._tools.specs()
        executed: list[dict[str, Any]] = []

        def _result(text: str, rounds: int) -> AgentResult:
            convo.append({"role": "assistant", "content": text})
            return AgentResult(text=text, rounds=rounds, tool_calls=executed, messages=convo)

        async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
            for round_i in range(1, self._s.max_tool_rounds + 1):
                msg = await self._chat(cli, [{"role": "system", "content": system}] + convo, specs, force_tool and round_i == 1)
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    return _result((msg.get("content") or "").strip(), round_i)

                # Keep the assistant turn (with its tool_calls) in the history.
                convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})

                # Execute EVERY requested call (there can be more than one).
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args = _parse_args(fn.get("arguments"))
                    self._trace.emit("tool", f"→ {name}", direction="out", data={"name": name, "args": args})
                    result = await self._tools.dispatch(name, args)
                    self._trace.emit("tool", f"← {name}", direction="in", data={"name": name, "result": result})
                    executed.append({"name": name, "args": args, "result": result})
                    convo.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })

            # Ran out of rounds — one closing answer without tools.
            final = await self._chat(cli, [{"role": "system", "content": system}] + convo, None, False)
            return _result((final.get("content") or "").strip(), self._s.max_tool_rounds)

    async def _chat(self, cli: httpx.AsyncClient, messages: list[dict[str, Any]], specs: list[dict] | None, force: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self._s.llm_model, "messages": messages}
        if specs:
            body["tools"] = specs
            body["tool_choice"] = "any" if force else "auto"
        self._trace.emit("llm-req", _last_user(messages), direction="out",
                         data={"model": self._s.llm_model, "messages": messages, "tools": [s["function"]["name"] for s in (specs or [])]})
        url = f"{self._s.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        # Retry transient rate-limits (429) / overloads (5xx) with backoff — the
        # provider clears them in a second or two; don't fail the whole turn.
        attempts = 4
        for attempt in range(attempts):
            try:
                resp = await cli.post(url, json=body, headers=headers)
                if resp.status_code in (429, 500, 502, 503) and attempt < attempts - 1:
                    delay = _retry_after(resp) or (0.6 * (2 ** attempt))
                    self._trace.emit("info", f"LLM {resp.status_code}; retry in {delay:.1f}s", level="warn")
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except httpx.HTTPError as err:
                self._trace.emit("error", f"LLM request failed: {_safe(err)}", level="error")
                raise
        msg = data["choices"][0]["message"]
        self._trace.emit("llm-rsp", (msg.get("content") or "").strip() or "(tool call)", direction="in",
                         data={"finish": data["choices"][0].get("finish_reason"), "tool_calls": msg.get("tool_calls")})
        return msg


class ConversationStore:
    """In-memory chat history per conversation id. Bounded + TTL'd, so the agent
    "sort of" remembers — recent turns carry context (follow-ups like "ja"), old
    conversations expire. History excludes the system prompt; trimming keeps clean
    user-turn boundaries so tool_call/tool message pairs are never split."""

    def __init__(self, ttl: float = 600.0, max_messages: int = 24) -> None:
        self._ttl = ttl
        self._max = max_messages
        self._store: dict[str, dict[str, Any]] = {}

    def get(self, conv_id: str) -> list[dict[str, Any]]:
        ent = self._store.get(conv_id)
        if not ent or (time.time() - ent["ts"]) > self._ttl:
            self._store.pop(conv_id, None)
            return []
        return ent["messages"]

    def update(self, conv_id: str, messages: list[dict[str, Any]]) -> None:
        self._store[conv_id] = {"messages": _trim(messages, self._max), "ts": time.time()}

    def clear(self, conv_id: str) -> None:
        self._store.pop(conv_id, None)


def _trim(msgs: list[dict[str, Any]], max_messages: int) -> list[dict[str, Any]]:
    """Keep the tail, but start at a user-turn boundary so an assistant's
    tool_calls always stay paired with their tool replies."""
    if len(msgs) <= max_messages:
        return msgs
    for i, m in enumerate(msgs):
        if m.get("role") == "user" and (len(msgs) - i) <= max_messages:
            return msgs[i:]
    # fallback: last user boundary
    user_idxs = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
    return msgs[user_idxs[-1]:] if user_idxs else msgs[-max_messages:]


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return min(float(raw), 10.0)  # cap so a turn can't stall forever
    except ValueError:
        return None


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
