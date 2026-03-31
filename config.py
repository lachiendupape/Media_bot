import os
from dotenv import load_dotenv

# Explicitly load .env from the project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__)))
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path=dotenv_path)

DATA_DIR = os.path.abspath(os.getenv("DATA_DIR", project_root))

TAUTULLI_URL = os.getenv("TAUTULLI_URL")
TAUTULLI_API_KEY = os.getenv("TAUTULLI_API_KEY")
RADARR_URL = os.getenv("RADARR_URL")
RADARR_API_KEY = os.getenv("RADARR_API_KEY")
SONARR_URL = os.getenv("SONARR_URL")
SONARR_API_KEY = os.getenv("SONARR_API_KEY")
LIDARR_URL = os.getenv("LIDARR_URL")
LIDARR_API_KEY = os.getenv("LIDARR_API_KEY")
NZBGET_URL = os.getenv("NZBGET_URL")
NZBGET_USER = os.getenv("NZBGET_USER")
NZBGET_PASS = os.getenv("NZBGET_PASS")
NZBHYDRA_URL = os.getenv("NZBHYDRA_URL")
NZBHYDRA_API_KEY = os.getenv("NZBHYDRA_API_KEY")

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

# Owner account — only this Plex username may delete media
OWNER_PLEX_USERNAME = os.getenv("OWNER_PLEX_USERNAME", "")

# Media request defaults
MEDIA_BOT_TAG = os.getenv("MEDIA_BOT_TAG", "media-bot")
KIDS_CONTENT_TAG = os.getenv("KIDS_CONTENT_TAG", "kids")

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

# Download quotas
QUOTA_ENABLED = os.getenv("QUOTA_ENABLED", "").lower() in ("1", "true", "yes")
# 0 means unlimited; positive integers set the daily cap
DAILY_MOVIE_QUOTA = int(os.getenv("DAILY_MOVIE_QUOTA", "3"))
# New key is TV series quota. Fall back to legacy TV season key for compatibility.
DAILY_TV_SERIES_QUOTA = int(os.getenv("DAILY_TV_SERIES_QUOTA", os.getenv("DAILY_TV_SEASON_QUOTA", "1")))

# Flask
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
if not FLASK_SECRET_KEY or len(FLASK_SECRET_KEY) < 16:
    raise RuntimeError(
        "FLASK_SECRET_KEY must be set in .env and be at least 16 characters. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )
