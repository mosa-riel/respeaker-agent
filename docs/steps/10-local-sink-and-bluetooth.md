# Step 10 — Local audio sink + host Bluetooth control

How-to log (append-only). Goal: play TTS out a host speaker (a paired Bluetooth A2DP
speaker) instead of the reSpeaker's tiny on-device speaker, and let the agent
re-pair/connect speakers itself.

## Why host-side (not HA)

Home Assistant OS **cannot** be a Bluetooth A2DP audio sink — its Bluetooth stack is
BLE-only (sensors, ESP32 proxies); there's no PulseAudio/PipeWire and no `media_player`
for a classic BT speaker. So audio output lives on the **agent host**: pair the speaker
there, route the agent's TTS to that PulseAudio sink. The reSpeaker mic is still the
input; only the output speaker moves.

> If `pactl list short sinks` shows a `bluez_sink.*` line, that host runs PulseAudio
> and is therefore a normal Linux box — **not** the HA OS appliance.

## Pair a speaker by hand (one-off)

```bash
bluetoothctl
  scan on            # find it, note the MAC
  pair  EB:AF:48:63:D6:5F
  trust EB:AF:48:63:D6:5F
  connect EB:AF:48:63:D6:5F
pactl list short sinks      # → bluez_sink.EB_AF_48_63_D6_5F.a2dp_sink
paplay --device=bluez_sink.EB_AF_48_63_D6_5F.a2dp_sink /usr/share/sounds/alsa/Front_Center.wav
```

## Route TTS to it

`config.json`:

```json
"audio_sink": "bluez_sink.EB_AF_48_63_D6_5F.a2dp_sink"
```

`""` = device speaker (default). `"default"` = host default sink. When set, the voice
turn streams the engine's int16 PCM straight into `paplay --raw` (`local_play.py`) and
awaits real playback end — no duration guessing. **Follow-up is disabled in local-sink
mode** (mic re-open rides the device's announce path, which we skip), so wake per turn.

## Let the agent switch speakers (opt-in)

`config.json`: `"bluetooth_control": true`. Registers locked-down agent tools:

- `bluetooth_list` — known devices + which are connected
- `bluetooth_scan` — ~12s discovery, returns MACs
- `bluetooth_connect` — pair+trust+connect a MAC, then set `audio_sink` to the new
  speaker (in-memory; save config via the API to persist)
- `bluetooth_disconnect`

Now "connect to the kitchen speaker" works by voice. Security (see
`reference/security.md`): fixed binary, no shell, allowlisted sub-commands, MAC
regex-validated — a non-MAC arg is rejected before any process spawns.

## Files

- `local_play.py` — PCM → PulseAudio sink via `paplay --raw`
- `bluetooth.py` — `bluetoothctl` wrapper + `bluetooth_tools(settings, trace)`
- `voice.py` — local-sink branch in `_handle_turn`
- `config.py` — `audio_sink`, `bluetooth_control`
- `web.py` — registers BT tools when enabled

Needs `paplay` + `bluetoothctl` on the host (`pulseaudio-utils`, `bluez`).
