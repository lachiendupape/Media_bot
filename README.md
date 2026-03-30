# Media Bot

A natural-language media library assistant that lets you search and manage your home media server through a web chat interface. Powered by a local LLM (Ollama) with tool-calling capabilities, it integrates with Radarr, Sonarr, and your Plex library.

## Features

- **Add movies** -- tell it a title and it searches Radarr, adds the movie, and kicks off a download.
- **Add TV series by season** -- searches Sonarr, lists available seasons, and lets you pick which to grab.
- **Search by person** -- queries a local credit cache to find movies and TV series by actor or director name, with optional filters for media type and role.
- **Reverse title lookup** -- ask who starred in or directed a specific movie/TV title in your library.
- **Fast disambiguation replies** -- if multiple close title matches are found, reply with `1`, `2`, `3`, etc. to choose.
- **Delete media (owner only)** -- the Plex server owner can remove movies or TV series (with file deletion) through the chat.
- **Disk space guard** -- blocks new downloads when any disk drops below 5% free space.
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
| `PLEX_MACHINE_ID` | Plex server machine identifier |
| `PLEX_CLIENT_ID` | A random UUID for this app |
| `OWNER_PLEX_USERNAME` | Plex username of the server owner (for delete permissions) |
| `OLLAMA_BASE_URL` | Ollama API URL (default `http://127.0.0.1:11434`, set automatically in Docker) |
| `OLLAMA_MODEL` | Ollama model name (default `qwen2.5:14b`) |

Generate a secret key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Find your Plex machine ID:

```bash
curl -s http://YOUR_PLEX_IP:32400/identity | grep machineIdentifier
```

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
| GET | `/health` | None | Service health check |
| POST | `/cache/rebuild` | Session or API key | Rebuild the credit cache |
| GET | `/auth/login` | None | Plex login page |
| GET | `/auth/start` | None | Initiate Plex OAuth flow |
| GET | `/auth/callback` | None | Plex OAuth callback |
| GET | `/auth/logout` | None | Clear session |

## Project Structure

```
Media_bot/
  main.py            -- Flask server, routes, authentication
  llm.py             -- LLM integration, tool schemas, handlers
  plex_auth.py       -- Plex OAuth PIN-based authentication
  config.py          -- Environment variable loading and validation
  Dockerfile         -- Container image definition
  docker-compose.yml -- Container orchestration
  pyproject.toml     -- Ruff linter configuration
  api/
    radarr.py        -- Radarr API wrapper and SQLite credit cache
    sonarr.py        -- Sonarr API wrapper
    lidarr.py        -- Lidarr API wrapper (status check only)
  templates/
    login.html       -- Plex sign-in page
    chat.html        -- Chat interface
  .env.example       -- Template for environment variables
  .github/
    workflows/
      ci.yml         -- GitHub Actions lint and syntax check
  requirements.txt   -- Python dependencies
```

## Deployment Behind a Reverse Proxy

If you expose Media Bot through Nginx or similar, set `FLASK_ENV=production` in your `.env` to enable the `Secure` flag on session cookies. The app reads `request.url_root` to build OAuth callback URLs, so it works automatically behind a proxy that sets `X-Forwarded-Proto`.

## Continuous Integration

No compilation step is required — Python is an interpreted language, so there is nothing to compile on the host before running the bot.

A GitHub Actions workflow (`.github/workflows/ci.yml`) runs automatically on every push and pull request. It:

1. **Installs dependencies** — `pip install -r requirements.txt` verifies the dependency tree resolves correctly.
2. **Lints with ruff** — catches style issues and unused imports.
3. **Checks syntax** — `python -m compileall` confirms every `.py` file parses without errors.

No secrets or service credentials are needed for CI; it only checks the Python source.

## License

This project is for personal use.
