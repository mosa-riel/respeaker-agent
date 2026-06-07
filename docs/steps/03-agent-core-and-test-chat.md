# Step 03 — Agent core (STT + multi-tool LLM loop) + test chat

**Date:** 2026-06-07
**Goal:** The provider-agnostic agent core — STT, the multi-round tool-calling LLM
loop, a manual `/api/run` endpoint, and a UI chat to test the whole flow without
the device owning voice yet.

## Loop shape (as the user specified)

    llm → tool (one OR MANY per turn) → action → result ─┐
     ↑                                                    │
     └──────────────── feed results back ─────────────────┘
    … repeats until a final answer → TTS

Each turn the model may emit several `tool_calls`; we run **every** one, feed the
real results back as `role:"tool"` messages, and loop (capped at
`max_tool_rounds`, default 5). We trust the executed result, never the model's
narration — 'verify, don't trust'. `tool_choice:"any"` forces a call on a must-act
turn.

## What was built

- `stt.py` — `STTClient.transcribe(pcm16, in_rate, language)`: wraps the device's
  mic PCM as WAV, POSTs `{stt_base_url}/audio/transcriptions` (Voxtral). Swap
  `stt_base_url` for a local server later.
- `tools.py` — `Tool` (name/description/JSON-schema params/async handler) +
  `ToolRegistry` (`specs()`, `dispatch()`). `demo_registry()` ships `get_time` +
  `echo` so the multi-tool loop is exercisable now. **MCP fans real tools in at
  phase 4.**
- `agent.py` — `AgentLoop.run(user_text, force_tool=, context=)` → `AgentResult`
  (text, rounds, executed tool_calls). Emits `llm-req`/`llm-rsp`/`tool` traces.
  `context=` is the seam for live home context injected from HA MCP (task #8).
- `config.py` — `system_prompt` (Dutch, voice-style, **tool-name-agnostic** — the
  home device list + tool names come from MCP, not hardcoded) + `max_tool_rounds`.
- `web.py` — `POST /api/run {text, speak?, force_tool?}` → runs the loop; with
  `speak` it synthesizes the reply and returns base64 WAV for browser playback.
  `GET /api/tools` lists the agent's tools. Both new fields added to the config
  allowlist (`system_prompt`, `max_tool_rounds`).
- `static/` — a **Test chat** card (Playground): prompt → `/api/run` → reply +
  expandable tool-call detail; `speak` toggle plays the TTS audio in-browser.
- `config.json` created from the example (gitignored) so the running app has the
  `voice_id` — TTS 400s without it (Voxtral requires a voice).

## Verified (real Mistral)

- Multi-tool in one turn: *"Hoe laat is het? En echo 'test'."* → `get_time` +
  `echo` both executed, 2 rounds, Dutch reply.
- `/api/run` with `speak`: *"Hoe laat is het?"* → `get_time` → reply →
  117804-byte WAV. End-to-end LLM→tool→TTS confirmed.

## Open / next

- **Voice pipeline (`voice.py`, task #5):** `subscribe_voice_assistant`
  (API_AUDIO) → wake → mic → `STTClient` → `AgentLoop` → `TTSClient` →
  `send_voice_assistant_audio`. Needs HA's pipeline for this device stopped.
- **MCP layer (task #8/phase 4):** real HA tools + cached/refreshable home context
  (periodic + voice command 'vernieuw apparaten') injected via `run(context=)`.
- **UI (tasks #6/#7):** sidebar + the clickable pipeline visualization (the chat
  becomes the Playground page).
- Security: `/api/run` is unauthenticated like the rest (localhost-bound). It calls
  the paid LLM/TTS on each request — fine locally; gate behind auth before any
  non-localhost bind. Noted in `reference/security.md`.
</content>
</invoke>
