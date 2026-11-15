# Step 10 — Local audio sink + host Bluetooth control

**Version:** 0.3.0
**Goal:** play TTS on a host speaker (a paired Bluetooth A2DP speaker) instead of the
reSpeaker's small speaker, and let the agent pair and connect speakers itself.

## Why host-side, not HA

Home Assistant OS cannot be a Bluetooth A2DP sink: its Bluetooth stack serves BLE (sensors,
ESP32 proxies), and it has no `media_player` for a classic Bluetooth speaker. So the speaker
is paired with the agent's host adapter and the agent plays TTS into that PulseAudio sink.
The reSpeaker mic stays the input.

## Pair a speaker by hand (once)

```bash
bluetoothctl
  scan on                      # find it, note the MAC
  pair    AA:BB:CC:DD:EE:FF
  trust   AA:BB:CC:DD:EE:FF
  connect AA:BB:CC:DD:EE:FF
pactl list short sinks         # → bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink
paplay --device=bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink /usr/share/sounds/alsa/Front_Center.wav
```

## Route TTS to it

`"audio_sink": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"`. Empty = device speaker (default);
`"default"` = host default sink. The turn then streams int16 PCM into `paplay --raw`
(`local_play.py`) and awaits the real end of playback. **Follow-up is disabled** in this mode
(mic re-open rides the device's announce path), so every turn needs the wake word.

## Let the agent switch speakers (opt-in)

`"bluetooth_control": true` registers locked-down tools: `bluetooth_list` (known devices,
which are connected), `bluetooth_scan` (~12 s discovery), `bluetooth_connect` (pair + trust +
connect a MAC, then set `audio_sink` to it) and `bluetooth_disconnect`. Fixed binary, no
shell, allowlisted sub-commands, MAC regex-validated (see
[security](../reference/security.md)).

## Files

`local_play.py` (PCM → sink via `paplay --raw`), `bluetooth.py` (`bluetoothctl` wrapper and
tools), `voice.py` (local-sink branch in `_handle_turn`), `config.py` (`audio_sink`,
`bluetooth_control`), `web.py` (registers the tools when enabled). Needs `pulseaudio-utils`
and `bluez`; the add-on needs `audio: true` and `host_dbus: true`.

## Later lessons (0.4.8–0.4.13)

An A2DP sink that has gone idle clips the first second of audio. `bt_lead_silence_ms` plays
silence before the first chunk, and a silence keepalive keeps the sink from suspending.
`wake_chime` plays a wake cue on the sink, with the mic gated meanwhile so the cue does not
trigger or pollute the recording.
