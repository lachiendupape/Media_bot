---
description: "Use when editing Media Bot Python backend files, especially llm.py, main.py, config.py, notifications.py, quota.py, plex_auth.py, observability.py, and api/*.py. Covers deterministic handlers, safe follow-up state, and validation expectations."
applyTo:
  - "main.py"
  - "llm.py"
  - "config.py"
  - "notifications.py"
  - "quota.py"
  - "plex_auth.py"
  - "observability.py"
  - "api/**/*.py"
name: "Media Bot Python Core"
---

# Media Bot Python Core

- Keep business logic deterministic in Python handlers. Use prompt text for intent shaping, not for critical authorization or data-integrity rules.
- Preserve multi-turn safety: pending selection, season choice, and kids/adult clarification flows must resume reliably from stored state.
- Do not bypass owner/admin checks for destructive operations.
- Integration calls to Radarr, Sonarr, Plex, and OMDb must fail gracefully with user-safe error messages.
- Add new configuration keys only through `config.py`, then document them in `.env.example` and `README.md`.
- Keep network-client logic in `api/` modules when practical; avoid duplicating external API wiring across unrelated files.
- Maintain backward-compatible tool handler contracts unless the task explicitly includes a breaking change.
- Before finishing Python changes, run baseline validation when available: `ruff check .` and `python -m compileall .`.
- For changes touching `llm.py` flow control, perform a quick manual `/chat` sanity check for movie add, TV add, and a follow-up disambiguation path.