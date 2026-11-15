# Step 03 — Agent core (STT + multi-tool LLM loop) + test chat

**Date:** 2026-06-07
**Goal:** the provider-agnostic agent core (STT, a multi-round tool-calling loop) plus a
manual `/api/run` endpoint and a UI chat to test it without the device.

## Loop shape

    llm → tool calls (one or many per round) → results ─┐
     ↑                                                   │
     └──────────────── feed results back ────────────────┘
    … until a final answer → TTS

Every `tool_call` in a round is executed and its real result fed back as a `role: "tool"`
message, up to `max_tool_rounds` (default 5). The executed result is trusted, never the
model's narration. `tool_choice: "any"` can force a call on a must-act turn.

## What was built

- `stt.py`: `STTClient.transcribe(pcm16, in_rate, language)` wraps mic PCM as WAV and posts
  it to `{stt_base_url}/audio/transcriptions`.
- `tools.py`: `Tool` (name, description, JSON-schema params, async handler) and
  `ToolRegistry` (`specs()`, `dispatch()`).
- `agent.py`: `AgentLoop.run(user_text, force_tool=, context=)` → `AgentResult` (text, rounds,
  executed tool calls); emits `llm-req`/`llm-rsp`/`tool` traces. `context=` is the seam for
  the home context (step 06).
- `config.py`: `system_prompt` (Dutch, voice style, tool-name-agnostic: device list and tool
  names come from MCP) and `max_tool_rounds`.
- `web.py`: `POST /api/run {text, speak?, force_tool?}` runs the loop; with `speak` it also
  returns base64 WAV. `GET /api/tools` lists the tools.
- UI: a test chat (later the **Playground** page) with expandable tool-call details and
  in-browser playback.

## Verified

Against Mistral with demo tools: two tool calls in one turn, both executed, Dutch reply;
`/api/run` with `speak` returned playable audio. Voxtral TTS needs a voice id; without one it
returns 400.

`/api/run` spends paid LLM/TTS tokens per call; it is localhost-only until auth exists
([security](../reference/security.md)).
