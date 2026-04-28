import os
from dotenv import load_dotenv

# Explicitly load .env from the project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__)))
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path=dotenv_path)

DATA_DIR = os.path.abspath(os.getenv("DATA_DIR", project_root))

TAUTULLI_URL = os.getenv("TAUTULLI_URL")
TAUTULLI_API_KEY = os.getenv("TAUTULLI_API_KEY")
TAUTULLI_WELCOME_ENABLED = os.getenv("TAUTULLI_WELCOME_ENABLED", "false").lower() in ("1", "true", "yes")
TAUTULLI_WELCOME_DAYS = int(os.getenv("TAUTULLI_WELCOME_DAYS", "7"))
TAUTULLI_WELCOME_TOP_SHOWS = int(os.getenv("TAUTULLI_WELCOME_TOP_SHOWS", "3"))
TAUTULLI_WELCOME_CACHE_SECONDS = int(os.getenv("TAUTULLI_WELCOME_CACHE_SECONDS", "900"))
TAUTULLI_PHASE2_ENABLED = os.getenv("TAUTULLI_PHASE2_ENABLED", "false").lower() in ("1", "true", "yes")
TAUTULLI_PHASE2_MIN_COMPLETION_RATIO = float(os.getenv("TAUTULLI_PHASE2_MIN_COMPLETION_RATIO", "0.9"))
TAUTULLI_PHASE2_MAX_SUGGESTIONS = int(os.getenv("TAUTULLI_PHASE2_MAX_SUGGESTIONS", "1"))
RADARR_URL = os.getenv("RADARR_URL")
RADARR_API_KEY = os.getenv("RADARR_API_KEY")
SONARR_URL = os.getenv("SONARR_URL")
SONARR_API_KEY = os.getenv("SONARR_API_KEY")
LIDARR_URL = os.getenv("LIDARR_URL")
LIDARR_API_KEY = os.getenv("LIDARR_API_KEY")

# Plex OAuth
PLEX_SERVER_URL = os.getenv("PLEX_SERVER_URL")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")
PLEX_MACHINE_ID = os.getenv("PLEX_MACHINE_ID")
PLEX_CLIENT_ID = os.getenv("PLEX_CLIENT_ID")
PLEX_APP_NAME = os.getenv("PLEX_APP_NAME", "Media Bot")

# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

# App / observability
APP_VERSION = os.getenv("APP_VERSION", os.getenv("MEDIA_BOT_VERSION", "dev"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
OBSERVABILITY_SERVICE_NAME = os.getenv("OBSERVABILITY_SERVICE_NAME", "media-bot")
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
BUG_REPORTS_FILE = os.path.join(DATA_DIR, "bug_reports.jsonl")
GITHUB_ISSUES_TOKEN = os.getenv("GITHUB_ISSUES_TOKEN", "")
GITHUB_ISSUES_REPO = os.getenv("GITHUB_ISSUES_REPO", "")
GITHUB_ISSUE_LABELS = [label.strip() for label in os.getenv("GITHUB_ISSUE_LABELS", "bug,reported-from-app").split(",") if label.strip()]

GITHUB_ISSUES_ENABLED = bool(GITHUB_ISSUES_TOKEN and GITHUB_ISSUES_REPO and "/" in GITHUB_ISSUES_REPO)
GITHUB_ISSUES_INCLUDE_CHAT_CONTEXT = os.getenv("GITHUB_ISSUES_INCLUDE_CHAT_CONTEXT", "false").lower() in ("1", "true", "yes")

# Owner account — only this Plex username may delete media
OWNER_PLEX_USERNAME = os.getenv("OWNER_PLEX_USERNAME", "")

# Media request defaults
MEDIA_BOT_TAG = os.getenv("MEDIA_BOT_TAG", "media-bot")
KIDS_CONTENT_TAG = os.getenv("KIDS_CONTENT_TAG", "kids")
ENABLE_REQUESTER_TAGGING = os.getenv("ENABLE_REQUESTER_TAGGING", "false").lower() in ("1", "true", "yes")
REQUESTER_TAG_PREFIX = os.getenv("REQUESTER_TAG_PREFIX", "")

# Radarr defaults
RADARR_MOVIE_ROOT = os.getenv("RADARR_MOVIE_ROOT", "/movies")
RADARR_KIDS_MOVIE_ROOT = os.getenv("RADARR_KIDS_MOVIE_ROOT", "/kidsmovies")
RADARR_DEFAULT_QUALITY_PROFILE = os.getenv("RADARR_DEFAULT_QUALITY_PROFILE", "HD-1080p")
RADARR_MINIMUM_AVAILABILITY = os.getenv("RADARR_MINIMUM_AVAILABILITY", "released")

# Sonarr defaults
SONARR_TV_ROOT = os.getenv("SONARR_TV_ROOT", "/tv")
SONARR_KIDS_TV_ROOT = os.getenv("SONARR_KIDS_TV_ROOT", "/kidstv")
SONARR_DEFAULT_QUALITY_PROFILE = os.getenv("SONARR_DEFAULT_QUALITY_PROFILE", "HD - 720p/1080p")
SONARR_SERIES_TYPE = os.getenv("SONARR_SERIES_TYPE", "standard")

# Kids/adults auto-classification
AUTO_CLASSIFY_KIDS_ENABLED = os.getenv("AUTO_CLASSIFY_KIDS_ENABLED", "true").lower() in ("1", "true", "yes")
OMDB_API_KEY = os.getenv("OMDB_API_KEY", "")
OMDB_TIMEOUT_SECONDS = int(os.getenv("OMDB_TIMEOUT_SECONDS", "5"))

# Download quotas
QUOTA_ENABLED = os.getenv("QUOTA_ENABLED", "").lower() in ("1", "true", "yes")
# 0 means unlimited; positive integers set the daily cap
DAILY_MOVIE_QUOTA = int(os.getenv("DAILY_MOVIE_QUOTA", "3"))
# New key is TV series quota. Fall back to legacy TV season key for compatibility.
DAILY_TV_SERIES_QUOTA = int(os.getenv("DAILY_TV_SERIES_QUOTA", os.getenv("DAILY_TV_SEASON_QUOTA", "1")))

# Webhook auth — optional shared secret for Radarr/Sonarr webhook endpoints
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
_FLASK_ENV = os.getenv("FLASK_ENV", "development").strip().lower()
if _FLASK_ENV == "production" and not WEBHOOK_SECRET:
    raise RuntimeError(
        "WEBHOOK_SECRET must be set when FLASK_ENV=production. "
        "This prevents unsigned public webhook requests."
    )

# Conversation memory (Phase 1 rollout)
CONVERSATION_MEMORY_ENABLED = os.getenv("CONVERSATION_MEMORY_ENABLED", "true").lower() in ("1", "true", "yes")
# Maximum number of turns (user + assistant pairs) to retain per identity
CONVERSATION_MEMORY_MAX_TURNS = int(os.getenv("CONVERSATION_MEMORY_MAX_TURNS", "20"))
# TTL in hours; 0 disables TTL expiration (only size-based trimming)
CONVERSATION_MEMORY_TTL_HOURS = int(os.getenv("CONVERSATION_MEMORY_TTL_HOURS", "24"))
# Run TTL cleanup on every Nth request (0 disables opportunistic cleanup)
CONVERSATION_MEMORY_CLEANUP_INTERVAL = int(os.getenv("CONVERSATION_MEMORY_CLEANUP_INTERVAL", "100"))
# Purge stored browser-session conversation history on logout.
CONVERSATION_MEMORY_PURGE_ON_LOGOUT = os.getenv("CONVERSATION_MEMORY_PURGE_ON_LOGOUT", "true").lower() in ("1", "true", "yes")

# Watch-based cleanup
CLEANUP_ENABLED = os.getenv("CLEANUP_ENABLED", "false").lower() in ("1", "true", "yes")
# How many days between re-checks for unwatched content (also the first-check window)
CLEANUP_CHECK_INTERVAL_DAYS = int(os.getenv("CLEANUP_CHECK_INTERVAL_DAYS", "7"))
# Hard-delete content after this many days regardless of watch status
CLEANUP_MAX_AGE_DAYS = int(os.getenv("CLEANUP_MAX_AGE_DAYS", "28"))
# Hour of day (UTC, 0-23) at which the cleanup job runs
CLEANUP_SCHEDULE_HOUR = int(os.getenv("CLEANUP_SCHEDULE_HOUR", "3"))
# Warn the user at download time if they have unwatched content older than this many days
CLEANUP_BACKLOG_WARN_DAYS = int(os.getenv("CLEANUP_BACKLOG_WARN_DAYS", "3"))
# Minimum distinct episodes watched in a season for it to count as "watched"
CLEANUP_MIN_WATCHED_EPISODES = int(os.getenv("CLEANUP_MIN_WATCHED_EPISODES", "1"))

# Flask
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
if not FLASK_SECRET_KEY or len(FLASK_SECRET_KEY) < 16:
    raise RuntimeError(
        "FLASK_SECRET_KEY must be set in .env and be at least 16 characters. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )
