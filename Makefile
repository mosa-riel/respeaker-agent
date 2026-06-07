.PHONY: sync run dev sbom

sync:           ## install/lock deps
	uv sync

run:            ## run the agent (http://127.0.0.1:8730)
	uv run respeaker-agent

dev:            ## run with autoreload
	uv run uvicorn respeaker_agent.web:app --reload --port 8730

sbom:           ## regenerate sbom.json (CycloneDX)
	./scripts/gen-sbom.sh
