# Documentation

Two kinds of docs:

- **`reference/`**: how things work now. Kept current.
- **`steps/`**: one log per milestone: what was built, why, and the lessons. Add a new step
  rather than rewriting an old one.

[PLAN.md](PLAN.md) has the goal, architecture and phases; [TODO.md](TODO.md) the open items.

## Reference
- [Deployment (Home Assistant add-ons)](reference/deployment.md)
- [Voice & audio](reference/voice-audio.md)
- [Security review](reference/security.md)
- [Open source & SBOM](reference/open-source.md)

## Steps
1. [Scaffold & live monitor](steps/01-scaffold-and-live-monitor.md)
2. [Pluggable TTS + audio layer](steps/02-tts-and-audio-config.md)
3. [Agent core + test chat](steps/03-agent-core-and-test-chat.md)
4. [MCP client layer + HA scoping](steps/04-mcp-layer-and-ha-scoping.md)
5. [Control latency + fixes](steps/05-control-latency-and-fixes.md)
6. [Grounding in the home context](steps/06-grounding-home-context.md)
7. [Voice pipeline over the native API](steps/07-voice-pipeline.md)
8. [Voice UX: bring-up, follow-up, flow-graph](steps/08-voice-ux-followup-flowgraph.md)
9. [HA add-ons + one repo per MCP](steps/09-ha-addons-and-mcp-split.md)
10. [Local audio sink + Bluetooth](steps/10-local-sink-and-bluetooth.md)
11. [Add-on config as source of truth + paged UI](steps/11-config-source-of-truth-and-ui-pages.md)
12. [Device list in the prompt + tool-loop fixes](steps/12-prompt-grounding-and-tool-loop.md)
13. [Turn detection, speech detection, speed](steps/13-realtime-turn-detection.md)
14. [External speaker, Dutch voice, answer-first UX](steps/14-external-speaker-and-dutch-voice.md)

ESPHome configs for both devices are in [`devices/`](../devices/).
