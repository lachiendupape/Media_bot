import os
from dotenv import load_dotenv

# Explicitly load .env from the project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__)))
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path=dotenv_path)

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
PLEX_MACHINE_ID = os.getenv("PLEX_MACHINE_ID")
PLEX_CLIENT_ID = os.getenv("PLEX_CLIENT_ID")
PLEX_APP_NAME = os.getenv("PLEX_APP_NAME", "Media Bot")

# Flask
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
