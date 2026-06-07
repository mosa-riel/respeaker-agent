#!/usr/bin/env bash
# Regenerate sbom.json (CycloneDX) from the locked environment.
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --quiet
uvx --quiet --from cyclonedx-bom cyclonedx-py environment .venv -o sbom.json
python3 - <<'PY'
import json
d = json.load(open("sbom.json"))
print(f"wrote sbom.json — {d.get('bomFormat')} {d.get('specVersion')}, {len(d.get('components', []))} components")
PY
