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
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Secrets, Settings
from .mcp_client import render_result
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
        # follow-ups like "ja" / "doe maar" keep context. `context` = the cached,
        # room-grouped device list (HomeContext) with entity_ids, so device commands go
        # straight to ha_call_service/ha_get_state without a search round. ~1k tokens —
        # far less than the tool specs, and it saves a whole LLM round per command.
        system = self._s.system_prompt
        system += f"\n\nHet is nu {_now_nl()}."
        # Tool-usage rules appended at RUNTIME so they always apply, even if a stored
        # system_prompt (frozen in config) is out of date.
        system += (
            "\n\n=== TOOLGEBRUIK ===\n"
            "- Bedienen (aan/uit, dimmen, openen/dicht): roep METEEN ha_call_service aan "
            "met de entity_id uit de apparatenlijst. Vraag de staat niet eerst op en zoek "
            "niet eerst. Het toolresultaat is je bevestiging — niet nog eens controleren.\n"
            "- Een hele ruimte (bv. 'alle lampen in de keuken'): bedien ALLE passende "
            "entity_ids van die ruimte uit de lijst.\n"
            "- ha_call_service: entity_id is precies ÉÉN string. Meerdere apparaten = "
            "meerdere calls in dezelfde beurt — geef NOOIT een lijst.\n"
            "- Staat of meetwaarde (is X aan, temperatuur): ha_get_state met de entity_id "
            "uit de lijst; meerdere tegelijk mag als lijst.\n"
            "- Staat iets niet in de lijst: ha_search_entities met area_filter=<ruimte> "
            "en/of domain_filter (geen losse woorden als 'lamp' in query) en negeer "
            "treffers buiten de gevraagde ruimte. Vind je het niet, zeg dat dan."
        )
        if context:
            system += f"\n\n=== APPARATEN ===\n{context}"
        convo: list[dict[str, Any]] = list(history or [])  # everything after system
        convo.append({"role": "user", "content": user_text})
        specs = self._tools.specs()
        executed: list[dict[str, Any]] = []

        def _result(text: str, rounds: int) -> AgentResult:
            text = _speakable(text)
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

                # Execute EVERY requested call (there can be more than one), concurrently —
                # "all lights in the kitchen" is N calls in one round; don't run them in series.
                calls = [(tc, tc.get("function", {}).get("name", ""), _parse_args(tc.get("function", {}).get("arguments")))
                         for tc in tool_calls]
                results = await asyncio.gather(*(self._dispatch(name, args) for _, name, args in calls))
                for (tc, name, args), result in zip(calls, results):
                    executed.append({"name": name, "args": args, "result": result})
                    convo.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        # Compact, LLM-friendly text (table for record lists) instead of
                        # raw JSON — fewer tokens, clearer "what happened".
                        "content": render_result(result),
                    })

            # Ran out of rounds — one closing answer without tools.
            final = await self._chat(cli, [{"role": "system", "content": system}] + convo, None, False)
            return _result((final.get("content") or "").strip(), self._s.max_tool_rounds)

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        self._trace.emit("tool", f"→ {name}", direction="out", data={"name": name, "args": args})
        result = await self._tools.dispatch(name, args)
        self._trace.emit("tool", f"← {name}", direction="in", data={"name": name, "result": result})
        return result

    async def _chat(self, cli: httpx.AsyncClient, messages: list[dict[str, Any]], specs: list[dict] | None, force: bool) -> dict[str, Any]:
        messages = _drop_empty_assistant(messages)
        body: dict[str, Any] = {"model": self._s.llm_model, "messages": messages, "temperature": self._s.llm_temperature}
        if specs:
            body["tools"] = specs
            body["tool_choice"] = "any" if force else "auto"
        self._trace.emit("llm-req", _last_user(messages), direction="out",
                         data={"model": self._s.llm_model, "messages": messages, "tools": [s["function"]["name"] for s in (specs or [])]})
        url = f"{self._s.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        # Retry transient rate-limits (429) / overloads (5xx) with backoff — the
        # provider clears them in a second or two; don't fail the whole turn.
        attempts = 6
        for attempt in range(attempts):
            try:
                resp = await cli.post(url, json=body, headers=headers)
                if resp.status_code in (429, 500, 502, 503) and attempt < attempts - 1:
                    # Broader backoff for tight free-tier RPS limits: 1,2,4,8,16s (capped),
                    # unless the provider sends a Retry-After.
                    delay = _retry_after(resp) or min(16.0, 2 ** attempt)
                    self._trace.emit("info", f"LLM {resp.status_code}; retry in {delay:.1f}s", level="warn")
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code >= 400:
                    # Surface the provider's actual reason (Mistral 400 explains *why* the
                    # payload was rejected — e.g. malformed messages/tool pairing).
                    detail = ""
                    try:
                        body_json = resp.json()
                        detail = body_json.get("message") or body_json.get("error") or resp.text[:400]
                    except Exception:  # noqa: BLE001
                        detail = resp.text[:400]
                    self._trace.emit("error", f"LLM {resp.status_code}: {detail}", level="error",
                                     data={"status": resp.status_code, "detail": str(detail)[:1000]})
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


def _drop_empty_assistant(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mistral 400s on an assistant message with neither content nor tool_calls — the
    model sometimes ends a turn empty (e.g. after a failed tool), and that empty turn
    gets stored in history and replayed forever. Strip those before sending. Safe: no
    `tool` message references an assistant turn that has no tool_calls."""
    return [
        m for m in messages
        if not (m.get("role") == "assistant"
                and not (m.get("content") or "").strip()
                and not m.get("tool_calls"))
    ]


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
        # Store only the conversational gist — not the intra-turn tool churn. See _compact_history.
        self._store[conv_id] = {"messages": _trim(_compact_history(messages), self._max), "ts": time.time()}

    def clear(self, conv_id: str) -> None:
        self._store.pop(conv_id, None)


def _compact_history(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist only the conversational gist — user turns + the model's final spoken
    answers. Intra-turn `tool_calls` / `tool` results (and empty assistants) are dropped:
    they bloat EVERY future prompt (a failed image-search saga re-sent each turn) and add
    nothing for follow-up context — the final answer already captures the outcome. The
    full churn still drives the live loop within a turn; it just isn't remembered."""
    out: list[dict[str, Any]] = []
    for m in msgs:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant" and not m.get("tool_calls") and (m.get("content") or "").strip():
            out.append({"role": "assistant", "content": m["content"]})
    return out


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


_DAGEN = ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag")
_MAANDEN = ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
            "september", "oktober", "november", "december")


def _now_nl() -> str:
    """Local date + time in Dutch for the prompt — answers "hoe laat / welke dag" without
    a tool round. Spelled out by hand: the slim image has no nl_NL locale."""
    t = time.localtime()
    return f"{_DAGEN[t.tm_wday]} {t.tm_mday} {_MAANDEN[t.tm_mon - 1]} {t.tm_year}, {t.tm_hour:02d}:{t.tm_min:02d}"


def _speakable(text: str) -> str:
    """The reply is read aloud: drop markdown markers the model sometimes adds despite
    the prompt (`**21 graden**` would otherwise reach TTS)."""
    return re.sub(r"\*+|`+|^#+\s*", "", text, flags=re.M).strip()


def _safe(err: Exception) -> str:
    msg = str(err) or err.__class__.__name__
    return msg.splitlines()[0][:200]
