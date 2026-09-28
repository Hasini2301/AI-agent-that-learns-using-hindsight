# SignalOps — Incident Response Copilot

A memory-first incident response workspace built for the HackwithHyderabad 3.0 brief. It captures operational learnings, surfaces relevant precedent, and produces a concise, evidence-linked response brief.

## Run locally

Requires Python 3.10+; no third-party dependencies.

```bash
python app.py
# open http://localhost:8000
```

Data is stored in `data/incidents.json`. The demo ships with realistic example incidents and works offline.

## Hindsight integration

The app supports the documented Hindsight REST retain/recall API. Set `SIGNALOPS_MEMORY_PROVIDER=hindsight` and configure the server-side values in `.env.example` before launching. The Cloud endpoint defaults to `https://api.hindsight.vectorize.io`; set your bank ID and API key from Hindsight Cloud. For local self-hosted HTTP only, explicitly enable `HINDSIGHT_ALLOW_INSECURE_HTTP=1`. Never put the API key in browser code or commit it. Without those settings, the app runs its deterministic offline local demo provider. See `docs/architecture.md` for error behavior and caveats.

## Main workflow

1. Select a service and describe the current symptoms.
2. Get similar historical incidents, confidence-scored response recommendations, and cited evidence.
3. Resolve / save a new incident to grow the team's durable memory.
4. Search previous incidents by phrase.

## Error contract

API failures return `{ "error": { "code": "...", "message": "..." } }` with an appropriate HTTP status. User-facing errors are shown inline; server logs retain diagnostic details without leaking secrets.

See `docs/architecture.md` for architecture, validation, failure catalog, test notes, and production hardening.
