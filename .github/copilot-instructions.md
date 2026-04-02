# Media Bot Project Instructions

## Purpose

Media Bot is a Flask-based chat application that uses an Ollama-hosted LLM to route user requests into deterministic tool handlers for Radarr, Sonarr, Plex, notifications, quotas, and observability. Prefer implementing business rules in Python rather than relying on the model to infer critical behavior.

## Architecture

- `main.py` owns Flask routes, auth/session handling, webhook endpoints, and chat HTTP behavior.
- `llm.py` owns system prompt construction, tool schemas, tool routing, pending multi-turn state, and request interpretation.
- `api/radarr.py`, `api/sonarr.py`, and `api/lidarr.py` are the service integration layer. Keep remote API details there rather than scattering request logic across the app.
- `config.py` is the single source of truth for environment-driven settings. Add new config there and document it in `.env.example` and `README.md`.
- `notifications.py`, `quota.py`, and `observability.py` contain isolated domain logic. Preserve those boundaries instead of adding unrelated logic to `main.py` or `llm.py`.

## Engineering Principles

- Preserve deterministic safety rails around LLM behavior. Ambiguous or destructive actions must be resolved in code with explicit state or confirmation, not by trusting free-form model output.
- Keep multi-turn request flows resumable. Title disambiguation, season selection, and kids-versus-adult routing must survive follow-up replies cleanly.
- Keep prompt behavior stable and explicit. Changes to instruction text should bias toward consistent English responses and predictable tool selection, while leaving final guardrails in code.
- Fail soft when external dependencies are unavailable. OMDb, Plex, Radarr, Sonarr, or webhook failures should degrade gracefully and return a useful user-facing message.
- Keep authorization checks explicit. Owner-only or destructive operations must stay gated in application code.
- Prefer focused fixes over broad prompt changes. If behavior can be made reliable in code, do that before expanding prompt complexity.

## Prompt and State Hardening

- Treat `llm.py` as a contract boundary: prompt edits must not silently break existing tool names, required arguments, or expected response structures.
- For any flow that asks a follow-up question, persist enough pending state to resume correctly on the next user message without re-querying external services when avoidable.
- Clear pending state only after a successful terminal action or an explicit cancellation path.
- If prompt wording changes alter user-visible behavior, add or update focused tests and run a quick manual chat sanity pass before release.
- Prefer deterministic parsing and normalization helpers in Python over embedding brittle parsing logic in prompt text.

## Working Rules

- For Python changes, validate with the same baseline used by CI when practical: `ruff check .` and `python -m compileall .`.
- For prompt-layer or tool-routing changes, also run a short live sanity check through `/chat` for movie add, TV add, disambiguation, and a kids/adult follow-up path.
- Update `README.md` for operator-visible behavior changes.
- Update `.env.example` for any new environment variable, changed default, or new integration.
- Preserve existing Docker compose separation: `docker-compose.dev.yml` for local development, `docker-compose.prod.yml` for production deployments.
- Do not change production deployment behavior, tags, or `.env` semantics unless the task is specifically about deployment or release management.

## Release Flow

- Develop and verify changes locally first, usually with `docker-compose.dev.yml`.
- Merge release-ready changes to `master` before cutting a release.
- Releases are published by `.github/workflows/release.yml` on version tags matching `v*`, or by manual workflow dispatch with a version input.
- The release workflow publishes GHCR images as `ghcr.io/lachiendupape/media-bot:<version>`, `<version-without-v>`, and `latest`.
- Production should be deployed from an explicit git ref or tag using `scripts/deploy-prod.ps1`, with `docker-compose.prod.yml` consuming `MEDIA_BOT_VERSION`.
- Prefer tagged releases for traceability. Use branch-based deploys only when intentionally deploying `latest`.

## Pointers

- See `README.md` for architecture, environment variables, and deployment details.
- See `.github/workflows/ci.yml` and `.github/workflows/release.yml` for the enforced CI and release contract.