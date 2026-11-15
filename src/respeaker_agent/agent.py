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
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

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
    spoken_early: bool = False  # `text` was already handed to on_say while the tools ran


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
        note: str | None = None,
        on_say: Callable[[str], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> AgentResult:
        # `history` = prior turns (user/assistant/tool messages, NO system) so
        # follow-ups like "ja" / "doe maar" keep context. `context` = the cached,
        # room-grouped device list (HomeContext) with entity_ids, so device commands go
        # straight to ha_call_service/ha_get_state without a search round. ~1k tokens —
        # far less than the tool specs, and it saves a whole LLM round per command.
        system = self._s.system_prompt
        # Tool-usage rules appended at RUNTIME so they always apply, even if a stored
        # system_prompt (frozen in config) is out of date.
        system += (
            "\n\n=== TOOLGEBRUIK ===\n"
            "- Bedienen (aan/uit, dimmen, openen/dicht): roep METEEN ha_call_service aan "
            "met de entity_id uit de apparatenlijst. Vraag de staat niet eerst op en zoek "
            "niet eerst. Vul in ha_call_service het veld 'zeg' met de korte zin die ik uitspreek "
            "als het lukt (bv. 'De eettafel is uit.'), één zin voor de hele opdracht, bij meerdere "
            "calls overal dezelfde. Lukt het, dan zeg ik die meteen; niet nog eens controleren.\n"
            "- Een hele ruimte (bv. 'alle lampen in de keuken'): bedien ALLE passende "
            "entity_ids van die ruimte uit de lijst.\n"
            "- ha_call_service: entity_id is precies ÉÉN string. Meerdere apparaten = "
            "meerdere calls in dezelfde beurt — geef NOOIT een lijst.\n"
            "- Staat of meetwaarde (is X aan, temperatuur): ha_get_state met de entity_id "
            "uit de lijst; meerdere tegelijk mag als lijst.\n"
            "- Staat iets niet in de lijst: ha_search_entities met area_filter=<ruimte> "
            "en/of domain_filter (geen losse woorden als 'lamp' in query) en negeer "
            "treffers buiten de gevraagde ruimte. Vind je het niet, zeg dat dan.\n"
            "- Het e-paper scherm in de huiskamer is een verlengstuk van je antwoord. Bij elke "
            "informatievraag (weer, route, plaats, feiten, uitleg, getallen, lijstjes, vergelijkingen) "
            "maak je een kaart: eerst screen_components (de bouwdoos), dan show_card; bij een route of "
            "plaats met een kaartje (map). Vraagt de gebruiker iets op het scherm te zetten of te "
            "tekenen, doe dat altijd. Bij opdrachten aan apparaten (lampen, rolluiken) en korte "
            "praatjes geen kaart.\n"
            "- Bij de EERSTE schermtool vul je 'zeg' met je gesproken antwoord: eerst kort het "
            "antwoord zelf, zo dat het zonder scherm te begrijpen is, dan dat je het op het scherm "
            "zet (bv. '... Ik zet de details op het scherm.'). Dat wordt meteen uitgesproken terwijl "
            "jij de kaart maakt; daarna hoef je niets meer te zeggen.\n"
            "- Werk parallel: alles wat niet van elkaar afhangt roep je in DEZELFDE ronde aan (bv. weer en "
            "route, verkeer en weer, twee zoekvragen, twee pagina's lezen, en screen_components zodra je weet "
            "dat er een kaart komt). Wacht alleen op een resultaat als je het nodig hebt voor de volgende stap.\n"
            "- Wegen, opritten, afsluitingen, werkzaamheden of hinder onderweg: gebruik verkeer (de officiële "
            "gegevens van Rijkswaterstaat en wegbeheerders, langs de route vanaf thuis), niet internet.\n"
            "- Beloof nooit iets zonder het te doen: zeg je dat je iets laat zien, roep dan in dezelfde "
            "beurt de schermtools aan.\n"
            "- Gaat een stap even duren (zoeken, route, weer, een kaart), zet dan een korte, natuurlijke zin "
            "als tekst naast je toolaanroep, zoals je in een gesprek zou zeggen ('Even kijken hoe het waait "
            "richting Sittard.'); die spreek ik meteen uit.\n"
            "- Plaatsen: 'thuis', 'hier' of geen startpunt is altijd het huis (het adres van Home Assistant).\n"
            "- Vindt een tool iets niet, of alleen iets gelijkends, zeg dat eerlijk (ook op de kaart) en doe niet "
            "alsof je het gevraagde hebt; vraag zo nodig wat er bedoeld wordt. Herhaal dezelfde zoekvraag niet "
            "in andere schrijfwijzen.\n"
            "- De vorige vragen en antwoorden (tot drie, van het afgelopen half uur) staan hierboven. Verwijst "
            "de gebruiker ergens naar ('dat', 'daar', 'laat het zien', 'en morgen?'), kijk daar dan eerst.\n"
            "- Zoek op internet hooguit twee keer per vraag; antwoord daarna met wat je gevonden hebt en zeg "
            "het eerlijk als het niet zeker is.\n"
            "- Gaat de vraag over nu of binnenkort (afsluitingen, evenementen, openingstijden, prijzen), gebruik "
            "dan alleen informatie waarvan je ziet dat ze van nu is (datum, jaar). Is een bron oud of zonder "
            "datum, of meldt een tool 'let_op', zeg dan dat je het niet zeker weet in plaats van het als feit te "
            "brengen. Noem nooit een weekdag bij een datum zonder te controleren dat die klopt met vandaag."
        )
        if context:
            system += f"\n\n=== APPARATEN ===\n{context}"
        convo: list[dict[str, Any]] = list(history or [])  # everything after system
        convo.append({"role": "user", "content": user_text})
        # The clock rides on this turn's question, not in the system prompt: a prompt whose
        # start changes every minute defeats the provider's prefix cache (hostyourai served
        # 6720 of ~6760 prompt tokens from cache with a stable prefix).
        asked = len(convo) - 1
        # `note` (e.g. the voice loop's follow-up rules) rides along the same way: sent with
        # this turn only, not kept in the history.
        stamped = {"role": "user", "content": f"{user_text}\n\n(Het is nu {_now_nl()}.)" + (f"\n{note}" if note else "")}

        def _sent() -> list[dict[str, Any]]:
            return [{"role": "system", "content": system}] + convo[:asked] + [stamped] + convo[asked + 1:]
        specs = _with_say(self._tools.specs())
        executed: list[dict[str, Any]] = []

        spoken = ""  # what on_say already spoke
        nudged = False  # pushed the model once to do the screen work it promised
        # Progress for voice: the model's own text next to tool calls is spoken at once; when it
        # has been quiet for _QUIET_S, a short line says what is going on (at most _PROGRESS_MAX).
        progress = {"last": time.monotonic(), "count": 0, "doing": random.choice(_THINKING), "said": set()}

        def _progress(text: str) -> None:
            if on_progress is None or spoken or progress["count"] >= _PROGRESS_MAX or text in progress["said"]:
                return
            progress.update(last=time.monotonic(), count=progress["count"] + 1)
            progress["said"].add(text)
            on_progress(text)

        async def _watch_quiet() -> None:
            while True:
                await asyncio.sleep(1.0)
                if time.monotonic() - progress["last"] >= _QUIET_S:
                    _progress(progress["doing"])
                    progress["last"] = time.monotonic()  # also when nothing was said: no busy loop

        watcher = asyncio.create_task(_watch_quiet()) if on_progress is not None else None

        def _result(text: str, rounds: int, early: bool = False) -> AgentResult:
            text = _speakable(text)
            convo.append({"role": "assistant", "content": text})
            return AgentResult(text=text, rounds=rounds, tool_calls=executed, messages=convo,
                               spoken_early=early or bool(spoken))

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
                for round_i in range(1, self._s.max_tool_rounds + 1):
                    progress["doing"] = random.choice(_THINKING if round_i == 1 else _STILL)
                    msg = await self._chat(cli, _sent(), specs, force_tool and round_i == 1)
                    tool_calls = msg.get("tool_calls") or []
                    if not tool_calls:
                        text = (msg.get("content") or "").strip()
                        # "...Ik zoek een kaartje om het te laten zien." and then nothing: speak that
                        # as the answer and let the model do what it promised (once).
                        looked_up = any(e["name"].split("__")[0] in _INFO_SERVERS for e in executed)
                        if (not nudged and round_i < self._s.max_tool_rounds
                                and (_PROMISE.search(text) or looked_up)
                                and not any(_is_screen(e["name"]) for e in executed)
                                and any(_is_screen(sp["function"]["name"]) for sp in specs)):
                            nudged = True
                            if on_say is not None and not spoken:
                                text = _speakable(text)
                                on_say(text)
                                spoken = text
                            convo.append({"role": "assistant", "content": text})
                            convo.append({"role": "user", "content": _NUDGE})
                            continue
                        return _result(text, round_i)

                    # Keep the assistant turn (with its tool_calls) in the history.
                    convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})

                    # Execute EVERY requested call (there can be more than one), concurrently —
                    # "all lights in the kitchen" is N calls in one round; don't run them in series.
                    calls = [(tc, tc.get("function", {}).get("name", ""), _parse_args(tc.get("function", {}).get("arguments")))
                             for tc in tool_calls]
                    says = [str(args.pop(_SAY, "") or "").strip() for _, _, args in calls]  # ours, not the server's
                    progress["doing"] = _doing(calls)
                    # A screen tool with 'zeg': speak it now (voice) while the card is being made.
                    if on_say is not None and not spoken and any(_is_screen(n) for _, n, _ in calls):
                        if (early := _speakable(next((x for x in says if x), ""))):
                            on_say(early)
                            spoken = early
                    # The model's own words next to the calls ("Ik probeer het nog een keer") are
                    # progress: speak them now. Not for service calls, where they are the answer,
                    # and not once the answer is spoken (the text often repeats it: said twice).
                    content = (msg.get("content") or "").strip()
                    if content and not all(n.endswith("ha_call_service") for _, n, _ in calls):
                        _progress(content)
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

                    # A device command that came with its spoken confirmation (the 'zeg' field, or
                    # text next to the calls): when every call was a service call and all
                    # succeeded, say that now. A second LLM round only to word "it's done" cost
                    # ~0.8 s; on any failure the loop goes on as before.
                    said = next((x for x in reversed(says) if x), "") or (msg.get("content") or "").strip()
                    if said and all(name.endswith("ha_call_service") for _, name, _ in calls) \
                            and all(_succeeded(r) for r in results):
                        return _result(said, round_i)
                    # The card is on screen and the answer was given with it: done, no closing round.
                    # Once the answer has been spoken, other calls next to the card (a last lookup)
                    # don't keep the turn going: that only led to a second, contradicting answer.
                    answer = spoken or said
                    renders = [r for (_, n, _), r in zip(calls, results)
                               if _is_screen(n) and not n.endswith("screen_components")]
                    if answer and renders and all(_screen_ok(r) for r in renders) \
                            and (spoken or all(_is_screen(n) for _, n, _ in calls)):
                        return _result(answer, round_i, early=bool(spoken) or answer in progress["said"])

                # Ran out of rounds — one closing answer without tools.
                final = await self._chat(cli, _sent(), None, False)
                return _result((final.get("content") or "").strip(), self._s.max_tool_rounds)
        finally:
            if watcher is not None:
                watcher.cancel()

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
                         data={"finish": data["choices"][0].get("finish_reason"), "tool_calls": msg.get("tool_calls"),
                               "usage": data.get("usage")})
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

    def keep_recent(self, conv_id: str, turns: int = 3) -> None:
        """End of a voice session: keep only the last `turns` questions with their answers, so
        a new question shortly after can still refer back ("laat dat zien", "en morgen?")."""
        ent = self._store.get(conv_id)
        if not ent:
            return
        msgs = ent["messages"]
        starts = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
        ent["messages"] = msgs[starts[-turns]:] if len(starts) > turns else msgs


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
            if m.get("content") == _NUDGE:  # our own push, not something the user said
                continue
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


_SAY = "zeg"


def _with_say(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give the service-call tool an extra 'zeg' field: the sentence to speak when it works.
    Models fill a schema field far more reliably than they add text next to a tool call
    (Qwen3 never did). The agent strips it before the call reaches the MCP server."""
    out = []
    for spec in specs:
        fn = spec.get("function", {})
        name = fn.get("name", "")
        if name.endswith("ha_call_service") or _is_screen(name):
            params = dict(fn.get("parameters") or {})
            props = dict(params.get("properties") or {})
            props[_SAY] = {"type": "string", "description": _SAY_SCREEN if _is_screen(name) else
                           "Korte Nederlandse zin die ik uitspreek als dit lukt, bv. 'De eettafel is uit.'"}
            params["properties"] = props
            spec = {**spec, "function": {**fn, "parameters": params}}
        out.append(spec)
    return out


_PROMISE = re.compile(r"op het scherm|laat .{0,40}zien|laten zien|kaart(je)?|ik zoek .{0,30}(op|uit)|ik (zet|maak|teken)", re.I)
_NUDGE = ("Zet het nu op het scherm als verlengstuk van je antwoord: maak een kaart (screen_components en "
          "show_card). Je antwoord is al uitgesproken, dus zeg verder niets.")
# Servers whose answers get a card on the screen (the user wants a card with every lookup).
_INFO_SERVERS = ("geo", "staan", "websearch")
_QUIET_S = 7.0  # speak a progress line after this long without speech
_PROGRESS_MAX = 3  # progress lines per turn


_THINKING = ["Momentje.", "Even denken.", "Eén momentje.", "Even kijken."]
_STILL = ["Nog heel even.", "Bijna.", "Nog even geduld.", "Ik ben er bijna."]


def _doing(calls: list[tuple[Any, str, dict]]) -> str:
    """A short, natural Dutch line for what the next tool round does, spoken when it has been
    quiet for a while; varied so it does not sound canned."""
    pick = random.choice
    for _, n, a in calls:
        tool = n.split("__")[-1]
        place = str(a.get("plaats") or "").strip()
        day = str(a.get("dag") or "").strip()
        where = "" if place.lower() in ("", "thuis", "hier") else f" in {place}"
        when = f" {day}" if day and day != "vandaag" else ""
        if tool == "route":
            dest = a.get("naar", "")
            return pick([f"Ik zoek de route naar {dest} op.", f"Even de route naar {dest} bekijken."])
        if tool == "weer" or a.get("domain") == "weather" or "weather." in str(a.get("entity_id", "")):
            return pick([f"Even kijken wat het weer{where}{when} doet.", f"Ik kijk even naar het weer{where}{when}."])
        if tool == "plaats_zoeken":
            return f"Even kijken waar {a.get('naam', 'dat')} ligt."
        if tool in ("screen_components", "show_card", "show_html", "show_dashboard"):
            return pick(["Ik zet het op het scherm.", "Ik maak er een kaartje van."])
        if tool == "web_search":
            return pick(["Even opzoeken.", "Ik kijk even op internet.", "Even zoeken."])
        if tool == "fetch_page":
            return pick(["Ik lees het even na.", "Even verder lezen."])
        if tool.startswith("ha_"):
            return pick(["Even kijken in huis.", "Ik kijk even hoe het ervoor staat."])
    return pick(_STILL)


_SCREEN_TOOLS = ("screen_components", "show_card", "show_page", "show_dashboard", "show_html", "show_text",
                 "show_image")
_SAY_SCREEN = ("Je gesproken antwoord, meteen uitgesproken terwijl het scherm wordt gemaakt: eerst kort "
               "het antwoord zelf, dan dat je het op het scherm zet. Alleen bij de eerste schermtool.")


def _is_screen(name: str) -> bool:
    return name.split("__")[-1] in _SCREEN_TOOLS


def _screen_ok(result: Any) -> bool:
    """A screen tool that put something on the screen (its reply says getoond/bijgewerkt/bewaard)."""
    if not isinstance(result, dict) or result.get("isError"):
        return False
    text = json.dumps(result, ensure_ascii=False).lower()
    return any(w in text for w in ("getoond", "bijgewerkt", "bewaard", "scherm leeg"))


def _succeeded(result: Any) -> bool:
    """A tool result the server marked as a success (ha-mcp: structured.success)."""
    if not isinstance(result, dict) or result.get("isError"):
        return False
    structured = result.get("structured")
    return isinstance(structured, dict) and structured.get("success") is True


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
