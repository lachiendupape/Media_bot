# Media Bot

A natural-language media library assistant that lets you search and manage your home media server through a web chat interface. Powered by a local LLM (Ollama) with tool-calling capabilities, it integrates with Radarr, Sonarr, and your Plex library.

## Features

- **Add movies** -- tell it a title and it searches Radarr, adds the movie, and kicks off a download.
- **Add TV series by season** -- searches Sonarr, lists available seasons, and lets you pick which to grab.
- **Title disambiguation** -- when multiple movies or series share a name (e.g. King Kong), the bot presents numbered options and lets you pick the correct one.
- **Search by person** -- queries a local credit cache to find movies and TV series by actor or director name, with optional filters for media type and role. Supports partial names, fuzzy matching, and decade filtering.
- **Reverse title lookup** -- ask who starred in or directed a specific movie/TV title in your library.
- **Recommendations** -- ask for movies or shows similar to a title in your library; the bot finds titles sharing cast or directors.
- **Delete media (owner only)** -- the Plex server owner can remove movies or TV series (with file deletion) through the chat.
- **Default media request profiles** -- applies configured Radarr/Sonarr roots, quality profiles, tags, and minimum availability automatically.
- **Kids routing** -- routes kids movies and shows into dedicated root folders, with auto-classification from metadata and prompt-based confirmation when ambiguous.
- **Download quotas** -- optional per-user daily download limits for movies and TV series, with per-user overrides and UTC midnight reset.
- **Disk space guard** -- blocks new downloads when any disk drops below 5% free space.
- **User bug reports** -- the chat UI can send issue reports with request IDs and optional debug context.
- **GitHub issue creation** -- bug reports can optionally create a GitHub issue when a repo and token are configured.
- **Download notifications** -- tracks who requested each download and delivers completion or failure notifications via in-chat banners and a polling endpoint. Radarr/Sonarr webhooks route events back to the requesting user.
- **Speaking styles** -- change how the bot responds with commands like "speak like a pirate", "robot mode", or "reset style". Supported styles: pirate, robot, sarcastic, shakespearean, enthusiastic.
- **Structured observability** -- JSON logs, request correlation IDs, optional Sentry errors, and optional OpenTelemetry traces.
- **Plex OAuth login** -- browser-based sign-in using your Plex account; only users with access to your server can use the bot.
- **API key access** -- programmatic access via `X-Api-Key` header for scripts and automation.
- **Web chat UI** -- dark-themed chat interface with user avatars and real-time responses.

## Architecture

```
Browser / API client
        |
   Flask (port 5000) [Docker container]
        |
   Ollama (qwen2.5:14b, port 11434) [host]
        |
   Tool handlers
    /    |    \    \
Radarr  Sonarr  Plex  SQLite credit cache
                        (actors + directors,
                         movies + TV series)
```

The LLM decides which tool to call based on the user's message. Tool handlers call the Radarr/Sonarr APIs directly over your LAN and return human-readable responses.

## Prerequisites

- [Ollama](https://ollama.com/) running locally with the `qwen2.5:14b` model pulled
- Radarr and Sonarr instances accessible on your network
- A Plex server (for OAuth authentication)
- Docker and Docker Compose (recommended), or Python 3.10+

## Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/lachiendupape/Media_bot.git
cd Media_bot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

At minimum you need to set:

| Variable | Description |
|----------|-------------|
| `RADARR_URL` | Radarr base URL (e.g. `http://YOUR_SERVER_IP:7878/radarr`) |
| `RADARR_API_KEY` | Radarr API key (Settings > General in Radarr) |
| `SONARR_URL` | Sonarr base URL |
| `SONARR_API_KEY` | Sonarr API key |
| `FLASK_SECRET_KEY` | Random string for session signing (see below) |
| `BOT_API_KEY` | Key for programmatic API access |
| `PLEX_SERVER_URL` | Your Plex server URL |
| `PLEX_TOKEN` | Optional Plex token used for TV cast lookups |
| `PLEX_MACHINE_ID` | Plex server machine identifier |
| `PLEX_CLIENT_ID` | A random UUID for this app |
| `OWNER_PLEX_USERNAME` | Plex username of the server owner (for delete permissions) |
| `OLLAMA_BASE_URL` | Ollama API URL (default `http://127.0.0.1:11434`, set automatically in Docker) |
| `OLLAMA_MODEL` | Default Ollama model name when not overridden by compose |
| `APP_VERSION` | Version label shown in UI and attached to bug reports |
| `LOG_LEVEL` | Backend log verbosity |
| `OBSERVABILITY_SERVICE_NAME` | Service name used by observability exporters |
| `SENTRY_DSN` | Optional backend error-reporting DSN |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Optional OpenTelemetry traces endpoint |
| `GITHUB_ISSUES_REPO` | Optional GitHub repo for issue creation, in `owner/repo` format |
| `GITHUB_ISSUES_TOKEN` | Optional GitHub token with permission to create issues |
| `GITHUB_ISSUE_LABELS` | Optional comma-separated labels for created issues |
| `WEBHOOK_SECRET` | Shared secret for Radarr/Sonarr webhook endpoints |
| `QUOTA_ENABLED` | Set to `true` to enforce per-user daily download limits |
| `DAILY_MOVIE_QUOTA` | Max movie downloads per user per day (0 = unlimited) |
| `DAILY_TV_SERIES_QUOTA` | Max TV series downloads per user per day (0 = unlimited) |

Useful optional defaults for automated media adds:

| Variable | Description |
|----------|-------------|
| `MEDIA_BOT_TAG` | Tag applied to all bot-created requests |
| `KIDS_CONTENT_TAG` | Additional tag applied to kids requests |
| `RADARR_MOVIE_ROOT` | Default Radarr movie root folder |
| `RADARR_KIDS_MOVIE_ROOT` | Radarr root folder for kids movies |
| `RADARR_DEFAULT_QUALITY_PROFILE` | Default Radarr quality profile name |
| `RADARR_MINIMUM_AVAILABILITY` | Default Radarr minimum availability (for example `released`) |
| `SONARR_TV_ROOT` | Default Sonarr TV root folder |
| `SONARR_KIDS_TV_ROOT` | Sonarr root folder for kids TV |
| `SONARR_DEFAULT_QUALITY_PROFILE` | Default Sonarr quality profile name |
| `SONARR_SERIES_TYPE` | Default Sonarr series type (for example `standard`) |
| `AUTO_CLASSIFY_KIDS_ENABLED` | Enable automatic kids/adults classification from metadata before prompting |
| `OMDB_API_KEY` | Optional OMDb API key for better kids/adults classification (especially TV) |
| `OMDB_TIMEOUT_SECONDS` | Timeout for OMDb lookups in seconds |

Generate a secret key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Find your Plex machine ID:

```bash
curl -s http://YOUR_PLEX_IP:32400/identity | grep machineIdentifier
```

For TV actor reverse lookup, set `PLEX_TOKEN` in `.env`. Sonarr v4 does not expose a working series credit endpoint, so Media Bot falls back to Plex metadata for TV cast.

### 4. Pull the Ollama model

```bash
ollama pull qwen2.5:14b
```

### 5. Start the server

**With Docker (recommended):**

```bash
docker compose up -d
```

This builds the image, starts the container, and connects to Ollama on your host machine. The credit cache is persisted in a Docker volume.

**With Docker (dev instance on localhost:5001):**

```bash
docker compose -f docker-compose.dev.yml up -d
```

This runs an isolated local dev instance on `http://127.0.0.1:5001` with separate cache volume data.
By default it uses `DEV_OLLAMA_MODEL` (falling back to `qwen2.5:7b`) so local feature testing works even when the larger production model is not installed.

Production deploys use `PROD_OLLAMA_MODEL` (falling back to `qwen2.5:7b`) via `docker-compose.prod.yml`.

Check status:

```bash
docker compose -f docker-compose.dev.yml ps
```

**Without Docker:**

```bash
python main.py
```

The server starts on `http://0.0.0.0:5000`. Open it in a browser to sign in with Plex, or use the API key header for programmatic access.

## Usage

### Web interface

Navigate to `http://localhost:5000` and sign in with your Plex account. Then chat naturally:

- "Add the movie Sinners"
- "Add the show Adolescence" (it will list seasons and ask which you want)
- "What movies do I have with Tom Hanks?"
- "What has Christopher Nolan directed?"
- "Who directed Goodfellas?"
- "Who starred in Severance?"
- "Movies similar to Interstellar"
- "Delete the movie Jaws 3" (owner only)

### API access

```bash
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: YOUR_BOT_API_KEY" \
  -d '{"message": "add the movie jaws 3"}'
```

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | Session | Web chat UI |
| POST | `/chat` | Session or API key | Send a message, get a response |
| POST | `/bug-report` | Session or API key | Submit a user bug report with optional debug context |
| GET | `/health` | None | Service health check |
| POST | `/cache/rebuild` | Session or API key | Rebuild the credit cache |
| GET | `/notifications` | Session or API key | Poll for pending download notifications |
| POST | `/webhooks/radarr` | None (if `WEBHOOK_SECRET` unset) / Webhook secret | Radarr download/health event webhook |
| POST | `/webhooks/sonarr` | None (if `WEBHOOK_SECRET` unset) / Webhook secret | Sonarr download/health event webhook |
| GET | `/auth/login` | None | Plex login page |
| GET | `/auth/start` | None | Initiate Plex OAuth flow |
| GET | `/auth/callback` | None | Plex OAuth callback |
| GET | `/auth/logout` | None | Clear session |

## Dev and Release Environments

- `docker-compose.yml`: default local/prod-like build on port 5000
- `docker-compose.dev.yml`: local-only dev stack on `127.0.0.1:5001`
- `docker-compose.prod.yml`: production stack pinned to a GHCR image tag via `MEDIA_BOT_VERSION`

Typical workflow:

1. Develop and test locally with `docker-compose.dev.yml`.
2. Publish a versioned image with the `Release` workflow (for example `v1.3.0`).
3. Deploy and validate in dev first (`Deploy Dev` workflow or `scripts/deploy-dev.ps1`).
4. Manually promote to prod (`Promote To Prod` workflow) after dev checks pass.

Release tagging example:

```bash
git tag v1.3.0
git push origin v1.3.0
```

Deploy example:

```powershell
./scripts/deploy-prod.ps1 -Ref v1.3.0
```

Dev deploy example:

```powershell
./scripts/deploy-dev.ps1 -Ref v1.3.0
```

The deploy script maps version tags like `v1.3.0` to the matching GHCR image tag.
For branch refs such as `main` or `master`, it deploys `latest`.
If the GHCR pull is denied or unavailable on the host, the script automatically
falls back to a local Docker build using the same target image tag.

Promotion checklist before prod:

1. `Deploy Dev` workflow succeeds for target ref/version.
2. Dev `/health` endpoint returns `running`.
3. Basic `/chat` smoke tests pass (including follow-up parsing like `1 please` and `season 1 please`).
4. Run `Promote To Prod` workflow with the tested version.

## Project Structure

```
Media_bot/
  main.py            -- Flask server, routes, authentication, webhooks
  llm.py             -- LLM integration, tool schemas, handlers
  notifications.py   -- Download notification tracking and delivery
  quota.py           -- Per-user daily download quota management
  plex_auth.py       -- Plex OAuth PIN-based authentication
  config.py          -- Environment variable loading and validation
  observability.py   -- Structured logging, tracing, and bug-report helpers
  Dockerfile         -- Container image definition
  docker-compose.yml -- Container orchestration
  docker-compose.dev.yml  -- Local-only development stack (127.0.0.1:5001)
  docker-compose.prod.yml -- Production stack using GHCR image tags
  pyproject.toml     -- Ruff linter configuration
  api/
    radarr.py        -- Radarr API wrapper and SQLite credit cache
    sonarr.py        -- Sonarr API wrapper
    lidarr.py        -- Lidarr API wrapper (status check only)
  scripts/
    deploy-dev.ps1   -- Deploy dev stack from a git ref/tag
    deploy-prod.ps1  -- Deploy stack from a git ref/tag (GitOps-style)
    security/
      run-baseline.ps1   -- Local baseline scanner (ZAP + Nuclei)
      targets-prod.example.txt -- Production target template
      targets-dev.example.txt  -- Dev/staging target template
  templates/
    login.html       -- Plex sign-in page
    chat.html        -- Chat interface
  .env.example       -- Template for environment variables
  .github/
    workflows/
      ci.yml         -- GitHub Actions lint and syntax check
      deploy-dev.yml -- GitHub Actions dev deployment and health validation
      promote-to-prod.yml -- GitHub Actions manual dev-first promotion to production
      release.yml    -- GitHub Actions Docker image publish on version tags
      security.yml   -- GitHub Actions scheduled/manual security scans
  requirements.txt   -- Python dependencies
```

## Deployment Behind a Reverse Proxy

If you expose Media Bot through Nginx or similar, run it with `FLASK_ENV=production` so session cookies use the `Secure` flag. The production compose file already sets this for you. The app reads `request.url_root` to build OAuth callback URLs, so it works automatically behind a proxy that sets `X-Forwarded-Proto`.

## Continuous Integration

No compilation step is required — Python is an interpreted language, so there is nothing to compile on the host before running the bot.

A GitHub Actions workflow (`.github/workflows/ci.yml`) runs automatically on every push and pull request. It:

1. **Installs dependencies** — `pip install -r requirements.txt` verifies the dependency tree resolves correctly.
2. **Lints with ruff** — catches style issues and unused imports.
3. **Checks syntax** — `python -m compileall` confirms every `.py` file parses without errors.

No secrets or service credentials are needed for CI; it only checks the Python source.

## Bug Reporting and Observability

The app now includes a lightweight reporting and observability starter:

- Every request gets a request ID returned in the `X-Request-ID` response header.
- `/chat` responses include the request ID so users can attach it to bug reports.
- The chat UI includes a `Report issue` button that sends the last prompt/response context with optional debug metadata.
- The same bug report flow can also create a GitHub issue when `GITHUB_ISSUES_REPO` and `GITHUB_ISSUES_TOKEN` are configured.
  - **GitHub issues are auto-categorized and labeled** based on description keywords (bug, enhancement, search, chat, ui, performance, security).
  - Issue titles are formatted as `[CATEGORY] description` for easy filtering.
  - All users can submit reports (no authentication restriction on issue creation).
- Backend logs are emitted as structured JSON for easier filtering and ingestion.
- Sentry can be enabled with `SENTRY_DSN`.
- OpenTelemetry traces can be enabled with `OTEL_EXPORTER_OTLP_ENDPOINT`.

Bug reports are stored locally in `DATA_DIR/bug_reports.jsonl`.

## GitOps Release Flow

The repository now includes a release workflow (`.github/workflows/release.yml`) that publishes versioned container images to GHCR when you push a tag like `v1.3.0`.

Use `docker-compose.prod.yml` with `MEDIA_BOT_VERSION` to declare the desired running version. Deploying from a git tag with `scripts/deploy-prod.ps1` makes release state traceable and reproducible from Git history.

## Security Scanning (Local + GitOps)

This repository includes a baseline security scanning setup for your front-end domains.

- Production targets example: `scripts/security/targets-prod.example.txt`
- Dev/staging targets example: `scripts/security/targets-dev.example.txt`
- Local override files (gitignored): `scripts/security/targets-prod.txt` and `scripts/security/targets-dev.txt`
- One-command local baseline runner: `scripts/security/run-baseline.ps1`
- Scheduled GitHub workflow: `.github/workflows/security.yml`

Create local target files from examples:

```powershell
Copy-Item scripts/security/targets-prod.example.txt scripts/security/targets-prod.txt
Copy-Item scripts/security/targets-dev.example.txt scripts/security/targets-dev.txt
```

### 1) One-command local baseline scan

Requirements:

- Docker
- Nuclei CLI is optional. If `nuclei` is not installed locally, the script falls back to `projectdiscovery/nuclei:latest` in Docker.

Important:

- Running the local scanner from a private/LAN IP can bypass reverse-proxy allowlists and produce misleading results for internet-facing security checks.
- The script will warn and stop by default on LAN/private networks.
- For a true external perspective, trigger the GitHub Actions security workflow instead.
- Use `-IgnoreLanWarning` only when you intentionally want an internal-network scan.

Run production profile:

```powershell
./scripts/security/run-baseline.ps1 -Profile prod
```

Run dev profile:

```powershell
./scripts/security/run-baseline.ps1 -Profile dev
```

Run both profiles:

```powershell
./scripts/security/run-baseline.ps1 -Profile both
```

Bypass the LAN warning intentionally:

```powershell
./scripts/security/run-baseline.ps1 -Profile prod -IgnoreLanWarning
```

Reports are written under `security-reports/<timestamp>/`.
These reports are local-only and gitignored.

### 2) CI security job (Nuclei + Trivy)

The `Security Scans` workflow runs:

- Trivy filesystem scan (dependency and config findings in SARIF)
- Nuclei web scan against selected target profile

Trigger manually from GitHub Actions with profile `prod`, `dev`, or `both`.

Workflow outputs are uploaded as GitHub Actions artifacts, not committed into the repository:

- Trivy SARIF is uploaded to GitHub code scanning and also stored as a workflow artifact.
- Nuclei output is uploaded as a workflow artifact.
- Security artifacts currently retain for 14 days.

### 3) Weekly scheduled scan profile

The workflow runs weekly (Monday 04:00 UTC) and defaults to the `prod` profile.

To include staging/dev in scheduled scans, populate `scripts/security/targets-dev.txt` with reachable dev URLs.

## License

This project is for personal use.
