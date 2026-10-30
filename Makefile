.PHONY: sync run dev sbom lock

sync:           ## install/lock deps
	uv sync

lock:           ## re-lock and export requirements.lock (the add-on image installs from it)
	uv lock && uv export --frozen --no-hashes --no-dev --no-emit-project -o requirements.lock

run:            ## run the agent (http://127.0.0.1:8730)
	uv run respeaker-agent

dev:            ## run with autoreload
	uv run uvicorn respeaker_agent.web:app --reload --port 8730 --timeout-graceful-shutdown 3

sbom:           ## regenerate sbom.json (CycloneDX)
	./scripts/gen-sbom.sh
