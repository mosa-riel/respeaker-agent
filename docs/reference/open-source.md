# Reference — Open-source & SBOM

Machine-readable SBOM: [`sbom.json`](../../sbom.json) (CycloneDX 1.6, 33 components).
Dependencies are locked via `uv.lock`.

## Direct dependencies

| Package | Role | License |
|---|---|---|
| aioesphomeapi | ESPHome native-API client (device + voice flow) | MIT |
| fastapi / starlette | Web API for the local UI | MIT / BSD-3-Clause |
| uvicorn (+ uvloop, httptools, watchfiles, websockets) | ASGI server | BSD-3-Clause / MIT |
| pydantic / pydantic-core | Validation | MIT |
| python-dotenv | Secret loading from `.env` | BSD-3-Clause |
| zeroconf | mDNS discovery (device reconnect) | **LGPL-2.1-or-later** |
| cryptography / noiseprotocol / chacha20poly1305-reuseable | ESPHome Noise transport | Apache-2.0 / BSD / MIT |
| protobuf | ESPHome API wire format | BSD-3-Clause |

**Frontend:** no JS build — vanilla HTML/JS. Styling lifted from
`mosa.cloud.sources/commander` (internal). Fonts: Poppins + Open Sans (SIL OFL 1.1).

## License posture

All permissive (MIT/BSD/Apache/PSF) except **`zeroconf` (LGPL-2.1-or-later)** — used
as an unmodified library (dynamic import); fine for a self-hosted, non-distributed
tool. Re-check if ever statically bundled or redistributed.

## Regenerate

```bash
uvx --from cyclonedx-bom cyclonedx-py environment .venv -o sbom.json
```
