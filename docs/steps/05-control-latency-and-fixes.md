# Step 05 — Control-command latency, conversation memory, fixes

**Date:** 2026-06-07
**Goal:** device commands ("zet de lamp in de keuken uit") under 3 s and reliable; plus a
recurring SSE crash, a config-save pitfall and follow-up questions.

## Latency: the tool count dominates

A lamp on/off command end to end through `/api/run`:

| Config | Latency | Acted? |
|---|---|---|
| mistral-medium + 80 HA tools | 18–28 s | sometimes not |
| mistral-medium + 4 tools | 2.8–7 s | yes |
| mistral-small + 4 tools | 1.6–2.5 s | yes, every time |

- **Curate tools per server**: `McpServer.enabled_tools` (empty = all; otherwise only those).
  HA scoped to a handful of control tools. This is the main latency lever and improves tool
  choice; it is curation, not security. It replaced step 04's allow/deny lists with a single
  allowlist that maps 1:1 to the UI checkboxes.
- **`mistral-small-latest`** became the default: with few tools it is reliable and ~2× faster
  than medium. "Small hallucinates tool calls" was a symptom of the large tool set.
- Searching by name can match both a light and a script with a similar name; injecting the
  real entity list (step 06) fixes that and removes the search round.

## Conversation memory

Each `/api/run` was stateless, so "ja" after a question back lost the context.
`ConversationStore` keeps history per `conversation_id` in memory (TTL, capped at clean user
turn boundaries so tool_call/tool pairs never split). `AgentLoop.run(history=)` threads it;
`/api/run` takes `conversation_id` and `reset`; the chat has a "New chat" button.

## Fixes and pitfalls

- **SSE stream crashed every 15 s** (`StopAsyncIteration`): a `wait_for` timeout on
  `__anext__` cancelled the generator's `q.get()` and ended the generator. Fix:
  `TraceBus.subscribe()/unsubscribe()` and poll the queue directly; cancelling `queue.get()`
  leaves the subscription intact.
- **Editing `config.json` under a running server:** `PUT /api/config` saves the in-memory
  settings and overwrote hand edits. Config saves now reload `mcp_servers` from disk; still,
  edit `config.json` only while stopped, or use the UI.
