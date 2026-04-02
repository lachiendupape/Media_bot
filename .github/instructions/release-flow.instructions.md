---
description: "Use when modifying Media Bot release workflows, GHCR publishing, production compose files, or deployment scripts. Covers version tag rules, image tags, and production deploy safety."
applyTo:
  - ".github/workflows/release.yml"
  - "docker-compose.prod.yml"
  - "scripts/deploy-prod.ps1"
name: "Media Bot Release Flow"
---

# Media Bot Release Flow

- Preserve the `v*` release tag contract unless the task explicitly changes release strategy.
- Keep GHCR publishing aligned with `.github/workflows/release.yml`, including versioned tags, version-without-`v`, and `latest`.
- Keep production deployments traceable through `MEDIA_BOT_VERSION` and `scripts/deploy-prod.ps1`.
- Prefer explicit tagged releases over implicit `latest` changes.
- Do not make production-only behavior depend on undocumented local state.
- If release inputs, tags, or deploy semantics change, update `README.md` in the same change.