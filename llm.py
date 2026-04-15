import json
import logging
import re
import threading
import time
import requests
from openai import OpenAI
from api.radarr import RadarrAPI, credit_cache
from api.sonarr import SonarrAPI
import config
import notifications
import quota
from observability import redact_sensitive_fields, start_span

log = logging.getLogger(__name__)

# Minimum free disk space percentage before blocking new downloads
_DISK_FREE_THRESHOLD = 0.05  # 5%

# Initialize OpenAI client pointed at Ollama
try:
    client = OpenAI(
        base_url=f"{config.OLLAMA_BASE_URL}/v1",
        api_key="ollama"
    )
except Exception as e:
    print(f"Failed to initialize OpenAI client: {e}")
    client = None

# Define tool schemas for LLM function calling
tools = [
    {
        "type": "function",
        "function": {
            "name": "add_radarr_movie",
            "description": "Searches for a movie title and adds the first result to the Radarr library.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The title of the movie to search for and add."
                    },
                    "is_kids": {
                        "type": "boolean",
                        "description": "Set true when this should be added to kids media folders."
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_sonarr_series",
            "description": "Searches for a TV series and adds a specific season to the Sonarr library. If no season number is given, returns the list of available seasons so the user can choose.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The title of the TV series to search for."
                    },
                    "season": {
                        "type": "integer",
                        "description": "The season number to add. Omit to see available seasons."
                    },
                    "is_kids": {
                        "type": "boolean",
                        "description": "Set true when this should be added to kids media folders."
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_by_person",
            "description": (
                "Searches the media library for movies or TV series featuring a specific actor, "
                "actress, or director. Use media_type='movie' for movies only, 'tv' for TV series "
                "only, or omit for both. Use role='actor' for cast searches, 'director' for "
                "director searches, or omit for both."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person_name": {
                        "type": "string",
                        "description": "The name of the actor, actress, or director to search for."
                    },
                    "media_type": {
                        "type": "string",
                        "enum": ["movie", "tv"],
                        "description": "Filter by media type: 'movie' or 'tv'. Omit to search both."
                    },
                    "role": {
                        "type": "string",
                        "enum": ["actor", "director"],
                        "description": "Filter by role: 'actor' or 'director'. Omit to search both."
                    }
                },
                "required": ["person_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_title_credits",
            "description": (
                "Searches the media library for cast and/or director credits for a specific movie "
                "or TV series title. Use media_type='movie' for movies only, 'tv' for TV series "
                "only, or omit for both. Use role='actor' for cast only, 'director' for directors "
                "only, or omit for both."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The movie or TV title to inspect for cast/director credits."
                    },
                    "media_type": {
                        "type": "string",
                        "enum": ["movie", "tv"],
                        "description": "Filter by media type: 'movie' or 'tv'. Omit to search both."
                    },
                    "role": {
                        "type": "string",
                        "enum": ["actor", "director"],
                        "description": "Filter by role: 'actor' or 'director'. Omit to include both."
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_movie",
            "description": "Removes a movie from the Radarr library. Only the server owner may call this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The title of the movie to delete."
                    },
                    "delete_files": {
                        "type": "boolean",
                        "description": "Also delete the media files from disk. Defaults to true."
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_tv_series",
            "description": (
                "Removes a TV series or a specific season of a TV series from the Sonarr library. "
                "If a season number is provided, only that season is unmonitored and its files are deleted. "
                "If no season is given, the entire series is removed. Only the server owner may call this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The title of the TV series to delete."
                    },
                    "season": {
                        "type": "integer",
                        "description": "The season number to delete. Omit to delete the entire series."
                    },
                    "delete_files": {
                        "type": "boolean",
                        "description": "Also delete the media files from disk. Defaults to true."
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_similar",
            "description": (
                "Recommends movies or TV series from the library that share cast or directors "
                "with a given title. Use when the user asks for recommendations similar to a "
                "specific movie or show they mention."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The reference movie or TV title to base recommendations on."
                    },
                    "media_type": {
                        "type": "string",
                        "enum": ["movie", "tv"],
                        "description": "Limit recommendations to 'movie' or 'tv'. Omit for both."
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_download_status",
            "description": (
                "Checks the current download queue in Radarr and Sonarr to show the status of "
                "in-progress downloads, including percentage complete, estimated time remaining, "
                "and any errors or warnings. Use when the user asks about download progress, "
                "whether something is ready, or if there are download issues."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
]

_KIDS_HINT_PATTERN = re.compile(
    r"\b(kids?|children|child|family|disney|pixar|animated|cartoon|young\s+kids?)\b",
    flags=re.IGNORECASE,
)
_ADULT_HINT_PATTERN = re.compile(
    r"\b(adults?|grown\s*ups?|mature|18\+|not\s+for\s+kids|for\s+adults?)\b",
    flags=re.IGNORECASE,
)

_KIDS_CERTIFICATIONS = {
    "G",
    "TVY",
    "TVY7",
    "TVG",
    "U",
    "PG",
    "TVPG",
}
_ADULT_CERTIFICATIONS = {
    "R",
    "NC17",
    "TV14",
    "TVMA",
    "MA15+",
    "R18+",
    "X",
}

_OMDB_SESSION = requests.Session()
_OMDB_CACHE: dict[tuple[str, str, str], bool | None] = {}
_OMDB_CACHE_LOCK = threading.Lock()


def _initial_kids_preference(title: str, is_kids: bool | None = None) -> bool | None:
    """Return an explicit/heuristic preference from user input only."""
    if is_kids is not None:
        return bool(is_kids)
    text = title or ""
    if _KIDS_HINT_PATTERN.search(text):
        return True
    if _ADULT_HINT_PATTERN.search(text):
        return False
    return None


def _normalize_certification(value: str) -> str:
    cert = (value or "").upper().strip()
    cert = cert.replace("US:", "").replace("TV-", "TV").replace(" ", "")
    cert = cert.replace("_", "")
    return cert


def _extract_certification_from_item(item: dict) -> str:
    if not item:
        return ""
    for key in ("certification", "contentRating", "rating", "rated", "mpaaRating"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val
    ratings = item.get("ratings") or item.get("certifications")
    if isinstance(ratings, list):
        for rating in ratings:
            if isinstance(rating, dict):
                for key in ("value", "rating", "name", "certification"):
                    val = rating.get(key)
                    if isinstance(val, str) and val.strip():
                        return val
    return ""


def _is_family_animation(item: dict) -> bool:
    genres = item.get("genres") or []
    normalized = set()
    for g in genres:
        if isinstance(g, str):
            normalized.add(g.strip().lower())
        elif isinstance(g, dict):
            name = g.get("name")
            if isinstance(name, str):
                normalized.add(name.strip().lower())
    return bool(
        normalized.intersection({"family", "children", "animation", "kids", "cartoon"})
    )


def _classify_from_metadata(item: dict) -> bool | None:
    cert = _normalize_certification(_extract_certification_from_item(item))
    if cert:
        if cert in _KIDS_CERTIFICATIONS:
            return True
        if cert in _ADULT_CERTIFICATIONS:
            return False
    if _is_family_animation(item):
        return True
    return None


def _classify_from_omdb(title: str, year: int | None, media_type: str) -> bool | None:
    """Best-effort OMDb classification; returns None when unavailable/ambiguous."""
    api_key = (config.OMDB_API_KEY or "").strip()
    if not config.AUTO_CLASSIFY_KIDS_ENABLED or not api_key or not title:
        return None

    lookup_type = "series" if media_type == "series" else "movie"
    cache_key = (title.strip().lower(), str(year or ""), lookup_type)
    with _OMDB_CACHE_LOCK:
        if cache_key in _OMDB_CACHE:
            return _OMDB_CACHE[cache_key]

    params = {
        "apikey": api_key,
        "t": title,
        "type": lookup_type,
    }
    if year:
        params["y"] = str(year)

    result: bool | None = None
    try:
        response = _OMDB_SESSION.get("https://www.omdbapi.com/", params=params, timeout=config.OMDB_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("Response", "")).lower() == "true":
            cert = _normalize_certification(payload.get("Rated", ""))
            if cert in _KIDS_CERTIFICATIONS:
                result = True
            elif cert in _ADULT_CERTIFICATIONS:
                result = False
            elif _is_family_animation({"genres": (payload.get("Genre") or "").split(",")}):
                result = True
    except Exception:
        # Best-effort only: keep behavior stable when OMDb is unavailable.
        result = None

    with _OMDB_CACHE_LOCK:
        _OMDB_CACHE[cache_key] = result
    return result


def _resolve_kids_classification(
    title: str,
    user_preference: bool | None,
    item: dict,
    media_type: str,
) -> bool | None:
    """Resolve kids routing from explicit preference -> metadata -> OMDb."""
    if user_preference is not None:
        return user_preference

    meta_decision = _classify_from_metadata(item)
    if meta_decision is not None:
        return meta_decision

    return _classify_from_omdb(title, item.get("year"), media_type)


def _looks_like_kids_request(title: str, is_kids: bool | None = None) -> bool:
    """Infer kids routing when caller did not explicitly provide *is_kids*."""
    initial = _initial_kids_preference(title, is_kids)
    return bool(initial) if initial is not None else False

def _check_disk_space():
    """Check available disk space via Radarr. Returns (ok, message).
    ok=False and a human-readable message when any disk is below 5% free."""
    try:
        radarr = RadarrAPI()
        disks = radarr.get_disk_space()
        if not disks:
            return True, ""
        for disk in disks:
            total = disk.get('totalSpace', 0)
            free = disk.get('freeSpace', 0)
            if total > 0 and (free / total) < _DISK_FREE_THRESHOLD:
                pct = free / total * 100
                free_gb = free / (1024 ** 3)
                return False, (
                    f"⚠️ Insufficient disk space: only {free_gb:.1f} GB free "
                    f"({pct:.1f}%) on {disk.get('path', 'storage')}. "
                    "Please free up space before adding new media."
                )
    except Exception as e:
        print(f"[DiskCheck] Warning: could not check disk space: {e}", flush=True)
    return True, ""


def _is_owner(user_info):
    """Return True if user_info belongs to the configured server owner.

    Ownership is determined in order:
    1. If OWNER_PLEX_USERNAME is configured, compare it to the authenticated username.
    2. Otherwise, fall back to the is_owner flag stored in the user's session (set at
       login time from the Plex resources API ``owned`` field).
    """
    if not user_info:
        return False
    owner = config.OWNER_PLEX_USERNAME.strip().lower()
    if owner:
        return user_info.get('username', '').lower() == owner
    return bool(user_info.get('is_owner'))


def _user_identity(user_info: dict | None) -> tuple[str, str]:
    """Return a ``(user_id, username)`` pair suitable for quota accounting.

    When ``user_info`` is available (browser/session auth) the Plex user ID and
    username are used.  For API-key users or unauthenticated requests a stable
    fallback identifier is returned so quota checks still function.
    """
    if user_info:
        return str(user_info.get('id', 'unknown')), user_info.get('username', 'unknown')
    return 'api_key', 'api_key'


def _requester_tag_from_username(username: str | None) -> str | None:
    """Build a safe requester tag from username when requester tagging is enabled."""
    if not config.ENABLE_REQUESTER_TAGGING:
        return None

    if not username:
        return None

    cleaned_username = re.sub(r"\s+", "-", str(username).strip().lower())
    cleaned_username = re.sub(r"[^a-z0-9._-]", "-", cleaned_username)
    cleaned_username = re.sub(r"-+", "-", cleaned_username).strip("-._")
    if not cleaned_username or cleaned_username in {"unknown", "api_key"}:
        return None

    cleaned_prefix = re.sub(r"\s+", "-", str(config.REQUESTER_TAG_PREFIX).strip().lower())
    cleaned_prefix = re.sub(r"[^a-z0-9._-]", "-", cleaned_prefix)
    cleaned_prefix = re.sub(r"-+", "-", cleaned_prefix).strip("-._")
    if cleaned_prefix:
        cleaned_prefix = f"{cleaned_prefix}-"

    tag = f"{cleaned_prefix}{cleaned_username}"
    tag = tag[:64].rstrip("-._")
    return tag or None


def _do_add_radarr_movie(radarr: "RadarrAPI", selected_movie: dict, is_kids: bool, user_id: str, username: str) -> str:
    """Performs the actual Radarr API call to add a movie, choosing the root folder based on the kids flag."""
    preferred_root = config.RADARR_KIDS_MOVIE_ROOT if is_kids else config.RADARR_MOVIE_ROOT
    root_folder = radarr.get_root_folder_by_path(preferred_root)
    if not root_folder:
        return "Failed to retrieve Radarr root folder."

    quality_profile = radarr.get_quality_profile_by_name(config.RADARR_DEFAULT_QUALITY_PROFILE)
    if not quality_profile:
        return "Failed to retrieve Radarr quality profiles."

    raw_tags = [config.MEDIA_BOT_TAG]
    if is_kids:
        raw_tags.append(config.KIDS_CONTENT_TAG)
    requester_tag = _requester_tag_from_username(username)
    if requester_tag:
        raw_tags.append(requester_tag)
    tags = [t for t in raw_tags if t and str(t).strip()]

    result, error = radarr.add_movie(
        selected_movie,
        root_folder['path'],
        quality_profile['id'],
        minimum_availability=config.RADARR_MINIMUM_AVAILABILITY,
        tags=tags,
    )
    if result:
        quota.record_download(user_id, username, "movie", selected_movie['title'])
        try:
            notifications.record_pending_download(user_id, username, selected_movie['title'], "movie")
        except Exception:
            log.warning("Failed to record pending download notification for movie")
        return f"Great news! '{selected_movie['title']} ({selected_movie.get('year', '')})' has been grabbed and is downloading now — it'll be with you shortly!"
    if error == 'already_exists':
        return f"'{selected_movie['title']} ({selected_movie.get('year', '')})' is already in your library — no need to add it again!"
    return f"Failed to add movie '{selected_movie['title']}': {error}"


def add_radarr_movie_handler(
    title: str,
    state: dict = None,
    preferred_tmdb_id: int = None,
    preferred_year: int = None,
    user_info: dict = None,
    is_kids: bool | None = None,
) -> str:
    ok, msg = _check_disk_space()
    if not ok:
        return msg

    user_id, username = _user_identity(user_info)
    allowed, quota_msg = quota.check_quota(user_id, username, "movie")
    if not allowed:
        return quota_msg

    initial_kids_preference = _initial_kids_preference(title, is_kids)
    radarr = RadarrAPI()
    movies = radarr.lookup_movie(title)
    if not movies:
        return f"Could not find any movies matching '{title}'."

    selected_movie = None
    if preferred_tmdb_id is not None:
        selected_movie = next((m for m in movies if m.get('tmdbId') == preferred_tmdb_id), None)
    elif preferred_year is not None:
        selected_movie = next((m for m in movies if m.get('year') == preferred_year), None)

    if selected_movie is None and len(movies) > 1 and preferred_tmdb_id is None:
        options = movies[:10]
        if state is not None:
            state.pop('pending_series_pick', None)
            state['pending_movie_add'] = {
                'query': title,
                'is_kids': initial_kids_preference,
                'options': [
                    {
                        'title': m.get('title', '?'),
                        'year': m.get('year'),
                        'tmdbId': m.get('tmdbId'),
                    }
                    for m in options
                ],
            }
        lines = [f"I found multiple movies matching '{title}'. Which version would you like to add?"]
        for idx, m in enumerate(options, start=1):
            lines.append(f"{idx}. {m.get('title', '?')} ({m.get('year', '?')})")
        lines.append("Reply with the number (for example: 1).")
        return "\n".join(lines)

    if selected_movie is None:
        selected_movie = movies[0]

    resolved_kids = _resolve_kids_classification(
        title=selected_movie.get('title', title),
        user_preference=initial_kids_preference,
        item=selected_movie,
        media_type='movie',
    )

    if resolved_kids is None and config.RADARR_KIDS_MOVIE_ROOT and state is not None:
        # state is required for multi-turn conversation; stateless API calls skip the prompt
        state['pending_kids_check'] = {
            'kind': 'movie',
            'query': title,
            'tmdbId': selected_movie.get('tmdbId'),
            'year': selected_movie.get('year'),
        }
        return (
            f"One quick question before I add '{selected_movie['title']} ({selected_movie.get('year', '')})' — "
            "is this a kids film or for adults? Reply with **kids** or **adults**."
        )

    final_kids = bool(resolved_kids) if resolved_kids is not None else False
    return _do_add_radarr_movie(radarr, selected_movie, is_kids=final_kids, user_id=user_id, username=username)

def add_sonarr_series_handler(
    title: str,
    season: int = None,
    state: dict = None,
    preferred_tvdb_id: int = None,
    preferred_year: int = None,
    user_info: dict = None,
    is_kids: bool | None = None,
) -> str:
    ok, msg = _check_disk_space()
    if not ok:
        return msg

    user_id, username = _user_identity(user_info)
    allowed, quota_msg = quota.check_quota(user_id, username, "tv_series")
    if not allowed:
        return quota_msg

    initial_kids_preference = _initial_kids_preference(title, is_kids)
    sonarr = SonarrAPI()
    series = sonarr.lookup_series(title)
    if not series:
        return f"Could not find any series matching '{title}'."

    selected_series = None
    if preferred_tvdb_id is not None:
        selected_series = next((s for s in series if s.get('tvdbId') == preferred_tvdb_id), None)
    elif preferred_year is not None:
        selected_series = next((s for s in series if s.get('year') == preferred_year), None)

    if selected_series is None and len(series) > 1 and preferred_tvdb_id is None and season is None:
        options = series[:10]
        if state is not None:
            state.pop('pending_movie_add', None)
            state['pending_series_pick'] = {
                'query': title,
                'is_kids': initial_kids_preference,
                'options': [
                    {
                        'title': s.get('title', '?'),
                        'year': s.get('year'),
                        'tvdbId': s.get('tvdbId'),
                    }
                    for s in options
                ],
            }
        lines = [f"I found multiple TV series matching '{title}'. Which one would you like to add?"]
        for idx, s in enumerate(options, start=1):
            lines.append(f"{idx}. {s.get('title', '?')} ({s.get('year', '?')})")
        lines.append("Reply with the number (for example: 1).")
        return "\n".join(lines)

    if selected_series is None:
        selected_series = series[0]

    resolved_kids = _resolve_kids_classification(
        title=selected_series.get('title', title),
        user_preference=initial_kids_preference,
        item=selected_series,
        media_type='series',
    )

    if resolved_kids is None and config.SONARR_KIDS_TV_ROOT and state is not None:
        state['pending_kids_check'] = {
            'kind': 'series',
            'query': title,
            'tvdbId': selected_series.get('tvdbId'),
            'year': selected_series.get('year'),
            'season': season,
        }
        return (
            f"One quick question before I add '{selected_series['title']} ({selected_series.get('year', '')})' — "
            "should this go to **kids TV** or **adults TV**? Reply with **kids** or **adults**."
        )

    is_kids_request = bool(resolved_kids) if resolved_kids is not None else False

    seasons = [s for s in selected_series.get('seasons', []) if s['seasonNumber'] > 0]

    # No season specified — list available seasons and ask
    if season is None:
        if not seasons:
            return f"'{selected_series['title']}' has no season information available."
        if state is not None:
            state['pending_series_add'] = {
                'title': selected_series.get('title', title),
                'year': selected_series.get('year'),
                'tvdbId': selected_series.get('tvdbId'),
                'is_kids': is_kids_request,
                'available_seasons': sorted(s['seasonNumber'] for s in seasons),
            }
        season_list = ", ".join(str(s['seasonNumber']) for s in seasons)
        return (
            f"'{selected_series['title']} ({selected_series.get('year', '')})' has "
            f"{len(seasons)} season{'s' if len(seasons) != 1 else ''}: {season_list}.\n"
            f"Which season would you like to add?"
        )

    # Validate the requested season exists
    valid_numbers = {s['seasonNumber'] for s in seasons}
    if season not in valid_numbers:
        return f"Season {season} doesn't exist for '{selected_series['title']}'. Available seasons: {', '.join(str(n) for n in sorted(valid_numbers))}."

    if state is not None:
        state.pop('pending_series_add', None)

    preferred_root = config.SONARR_KIDS_TV_ROOT if is_kids_request else config.SONARR_TV_ROOT
    root_folder = sonarr.get_root_folder_by_path(preferred_root)
    if not root_folder:
        return "Failed to retrieve Sonarr root folder."

    quality_profile = sonarr.get_quality_profile_by_name(config.SONARR_DEFAULT_QUALITY_PROFILE)
    if not quality_profile:
        return "Failed to retrieve Sonarr quality profiles."

    raw_tags = [config.MEDIA_BOT_TAG]
    if is_kids_request:
        raw_tags.append(config.KIDS_CONTENT_TAG)
    requester_tag = _requester_tag_from_username(username)
    if requester_tag:
        raw_tags.append(requester_tag)
    tags = [t for t in raw_tags if t and str(t).strip()]

    result, error = sonarr.add_series(
        selected_series,
        root_folder['path'],
        quality_profile['id'],
        season_number=season,
        series_type=config.SONARR_SERIES_TYPE,
        tags=tags,
    )

    if result:
        quota.record_download(user_id, username, "tv_series", selected_series['title'])
        try:
            notifications.record_pending_download(user_id, username, selected_series['title'], "tv_season")
        except Exception:
            log.warning("Failed to record pending download notification for series")
        return f"Great news! '{selected_series['title']}' Season {season} has been grabbed and is downloading now — it'll be with you shortly!"
    if error == 'already_exists':
        # Series exists in library — check if the requested season is already monitored
        tvdb_id = selected_series.get('tvdbId')
        existing = sonarr.get_series_by_tvdb_id(tvdb_id) if tvdb_id else None
        if existing:
            existing_season = next((s for s in existing.get('seasons', []) if s['seasonNumber'] == season), None)
            if existing_season and existing_season.get('monitored'):
                return (
                    f"Season {season} of '{existing['title']}' is already in your library "
                    f"— no need to add it again!"
                )
            # Season is not monitored — enable it and trigger a search
            if existing_season:
                existing_season['monitored'] = True
            updated, update_error = sonarr.update_series(existing['id'], existing)
            if updated:
                sonarr.search_season(existing['id'], season)
                quota.record_download(user_id, username, "tv_series", existing['title'])
                try:
                    notifications.record_pending_download(user_id, username, existing['title'], "tv_season")
                except Exception:
                    log.warning("Failed to record pending download notification for series")
                return (
                    f"Great news! '{existing['title']}' Season {season} has been grabbed "
                    f"and is downloading now — it'll be with you shortly!"
                )
            return f"Failed to update '{selected_series['title']}': {update_error}"
        return f"'{selected_series['title']} ({selected_series.get('year', '')})' is already in your library — no need to add it again!"
    return f"Failed to add TV series '{selected_series['title']}': {error}"

def check_download_status_handler() -> str:
    """Query the Radarr and Sonarr download queues and return a formatted status summary."""
    radarr = RadarrAPI()
    sonarr = SonarrAPI()

    lines = []

    # --- Movies (Radarr) ---
    try:
        movie_queue = radarr.get_queue()
    except Exception as e:
        movie_queue = None
        lines.append(f"⚠️ Could not reach Radarr: {e}")

    if movie_queue is None:
        # Treat a None queue as an error condition rather than as an empty queue.
        # If no specific Radarr warning has been added yet, add a generic one.
        if not any("Radarr" in line for line in lines):
            lines.append("⚠️ Could not reach Radarr (queue unavailable)")
        movie_records = []
    else:
        movie_records = movie_queue.get('records', [])
    for item in movie_records:
        movie = item.get('movie') or {}
        title = movie.get('title') or item.get('title', 'Unknown')
        year = movie.get('year', '')
        label = f"{title} ({year})" if year else title

        size = item.get('size', 0)
        sizeleft = item.get('sizeleft', 0)
        progress = round((size - sizeleft) / size * 100) if size > 0 and size >= sizeleft else None
        timeleft = item.get('timeleft', '')
        status = item.get('status', 'unknown')
        tracked = item.get('trackedDownloadStatus', 'ok')
        status_msgs = [sm.get('title', '') for sm in item.get('statusMessages', []) if sm.get('title')]

        if tracked in ('warning', 'error'):
            icon = '⚠️' if tracked == 'warning' else '❌'
            detail = ', '.join(status_msgs) if status_msgs else tracked
            lines.append(f"  {icon} 🎬 {label} — {detail}")
        elif status in ('completed', 'importPending', 'importing', 'imported'):
            lines.append(f"  ✅ 🎬 {label} — ready to import")
        elif timeleft and progress is not None and progress < 100:
            lines.append(f"  ⏬ 🎬 {label} — {progress}% (~{timeleft} remaining)")
        else:
            lines.append(f"  ⏬ 🎬 {label} — {status}")

    # --- TV Series (Sonarr) ---
    try:
        tv_queue = sonarr.get_queue()
    except Exception as e:
        tv_queue = None
        lines.append(f"⚠️ Could not reach Sonarr: {e}")

    if tv_queue is None:
        tv_records = []
    else:
        tv_records = tv_queue.get('records', [])

    for item in tv_records:
        series = item.get('series') or {}
        episode = item.get('episode') or {}
        title = series.get('title', 'Unknown')
        season = episode.get('seasonNumber')
        ep_num = episode.get('episodeNumber')
        ep_info = f" S{season:02d}E{ep_num:02d}" if season is not None and ep_num is not None else ""

        size = item.get('size', 0)
        sizeleft = item.get('sizeleft', 0)
        progress = round((size - sizeleft) / size * 100) if size > 0 and size >= sizeleft else None
        timeleft = item.get('timeleft', '')
        status = item.get('status', 'unknown')
        tracked = item.get('trackedDownloadStatus', 'ok')
        status_msgs = [sm.get('title', '') for sm in item.get('statusMessages', []) if sm.get('title')]

        if tracked in ('warning', 'error'):
            icon = '⚠️' if tracked == 'warning' else '❌'
            detail = ', '.join(status_msgs) if status_msgs else tracked
            lines.append(f"  {icon} 📺 {title}{ep_info} — {detail}")
        elif status in ('completed', 'importPending', 'importing', 'imported'):
            lines.append(f"  ✅ 📺 {title}{ep_info} — ready to import")
        elif timeleft and progress is not None and progress < 100:
            lines.append(f"  ⏬ 📺 {title}{ep_info} — {progress}% (~{timeleft} remaining)")
        else:
            lines.append(f"  ⏬ 📺 {title}{ep_info} — {status}")

    if not lines:
        return "✅ No active downloads — your queue is empty."

    return "📥 Current downloads:\n\n" + "\n".join(lines)


def _format_person_results(results: list, display_name: str) -> str:
    """Format a flat list of person credit results under a single display name."""
    lines = [f"Results for '{display_name}' ({len(results)} title{'s' if len(results) != 1 else ''}):"]
    for m in results:
        status = "downloaded" if m['hasFile'] else "monitored"
        label = "📺" if m['media_type'] == 'tv' else "🎬"
        if m['role'] == 'director':
            credit_info = "as Director"
        else:
            credit_info = f"as {m['character']}" if m.get('character') else ""
        base = f"  {label} {m['title']} ({m['year']})"
        credit_part = f" {credit_info}" if credit_info else ""
        lines.append(f"{base}{credit_part} [{status}]")
    return "\n".join(lines)



def search_by_person_handler(person_name: str, media_type: str = None, role: str = None, state: dict = None) -> str:
    results = credit_cache.search(person_name, media_type=media_type, role=role)
    if results is None:
        return "The credit search index is still being built. Please try again in a moment."
    if not results:
        if credit_cache.entry_count == 0:
            # Self-heal: if cache exists but has no rows, kick off a rebuild.
            threading.Thread(target=credit_cache.build, daemon=True).start()
            return (
                "I could not find any credit data yet. I have started rebuilding the credit index "
                "from Radarr/Sonarr in the background. Please try this search again in about a minute."
            )
        scope = []
        if media_type:
            scope.append("movies" if media_type == "movie" else "TV series")
        if role:
            scope.append(role + "s")
        scope_str = " ".join(scope) if scope else "the library"
        return f"No results for '{person_name}' found in {scope_str}."

    # Detect partial/first-name match: multiple distinct full names returned
    distinct_names = list(dict.fromkeys(m['person_name'] for m in results))
    multiple_people = len(distinct_names) > 1

    # Store state so decade follow-ups can narrow down
    if state is not None:
        state['last_person_search'] = {
            'query': person_name,
            'results': results,
            'distinct_names': distinct_names,
        }

    if multiple_people:
        lines = [f"Found {len(distinct_names)} people matching '{person_name}' — here they all are:"]
        for full_name in distinct_names:
            person_results = [m for m in results if m['person_name'] == full_name]
            lines.append(f"\n\u2022 {full_name} ({len(person_results)} title{'s' if len(person_results) != 1 else ''}):")
            for m in person_results:
                status = "downloaded" if m['hasFile'] else "monitored"
                label = "📺" if m['media_type'] == 'tv' else "🎬"
                if m['role'] == 'director':
                    credit_info = "as Director"
                else:
                    credit_info = f"as {m['character']}" if m.get('character') else ""
                base = f"    {label} {m['title']} ({m['year']})"
                credit_part = f" {credit_info}" if credit_info else ""
                lines.append(f"{base}{credit_part} [{status}]")
        lines.append(f"\nOnce you know the full name, just ask e.g. \"what has {distinct_names[0]} starred in\".")
        return "\n".join(lines)

    # Single person — show full name from results (fixes first-name-only follow-up)
    matched_name = distinct_names[0]
    return _format_person_results(results, matched_name)


def search_title_credits_handler(title: str, media_type: str = None, role: str = None, state: dict = None) -> str:
    results = credit_cache.search_title_credits(title, media_type=media_type, role=role)
    if results is None:
        return "The credit search index is still being built. Please try again in a moment."
    if not results:
        scope = []
        if media_type:
            scope.append("movies" if media_type == "movie" else "TV series")
        if role:
            scope.append(role + "s")
        scope_str = " ".join(scope) if scope else "the library"
        return f"No results for '{title}' found in {scope_str}."

    return _format_title_credits_results(results, title=title, role=role, state=state)


def _format_title_credits_results(results, title: str = None, role: str = None, state: dict = None) -> str:
    """Format title credit rows and optionally populate disambiguation state."""
    if not results:
        query_title = title or "that title"
        return f"No results for '{query_title}' found in the library."

    # Group by matching title and format directors + cast for each matched title.
    grouped = {}
    for r in results:
        key = (r['title'], r['year'], r['media_type'])
        grouped.setdefault(key, []).append(r)

    # If partial matching found multiple distinct titles, ask the user to disambiguate.
    normalized_query = title.strip().lower() if title else ""
    exact_title_matches = [k for k in grouped if k[0].strip().lower() == normalized_query]
    if len(grouped) > 1 and not exact_title_matches:
        ordered_options = sorted(grouped.keys(), key=lambda x: (x[0].lower(), x[1] or 0, x[2]))
        options = []
        for idx, (matched_title, year, kind) in enumerate(ordered_options, start=1):
            label = "Movie" if kind == 'movie' else "TV"
            options.append(f"{idx}. {matched_title} ({year}) [{label}]")

        if state is not None:
            state['pending_title_lookup'] = {
                'options': [
                    {'title': t, 'year': y, 'media_type': k}
                    for (t, y, k) in ordered_options
                ],
                'role': role,
                'query_title': title,
            }

        return (
            f"I found multiple titles similar to '{title}'. Which one did you mean?\n"
            + "\n".join(options[:10])
            + "\nReply with the number (for example: 1)."
        )

    if state is not None:
        state.pop('pending_title_lookup', None)

    lines = []
    for (matched_title, year, kind), credits in grouped.items():
        label = "Movie" if kind == 'movie' else "TV"
        lines.append(f"{label}: {matched_title} ({year})")

        directors = []
        actors = []
        for c in credits:
            if c['role'] == 'director':
                if c['person_name'] not in directors:
                    directors.append(c['person_name'])
            elif c['role'] == 'actor':
                actor_display = c['person_name']
                if c.get('character'):
                    actor_display = f"{actor_display} as {c['character']}"
                if actor_display not in actors:
                    actors.append(actor_display)

        if role in (None, 'director'):
            if directors:
                if len(directors) == 1:
                    lines.append(f"Director: {directors[0]}")
                else:
                    lines.append("Directors:")
                    for d in directors:
                        lines.append(f"- {d}")
            else:
                lines.append("Director: none found")

        if role in (None, 'actor'):
            if actors:
                lines.append("Top billed cast:")
                for actor in actors[:10]:
                    lines.append(f"- {actor}")
                if len(actors) > 10:
                    lines.append(f"- ...and {len(actors) - 10} more")
            else:
                lines.append("Cast: none found")

        lines.append("")

    return "\n".join(lines).strip()


def _extract_number_from_message(text: str) -> int | None:
    """Extract a leading integer from a user message, ignoring trailing words like 'please'.

    The word-boundary assertion ensures strings like '1abc' do not match; only digits
    followed by whitespace, punctuation, or end-of-string are accepted.
    """
    m = re.match(r'^(\d+)\b', text.strip())
    return int(m.group(1)) if m else None


def _extract_season_from_message(text: str) -> int | None:
    """Extract a season number from a user message.

    Accepts forms like: '1', 'season 1', 'season 1 please', '1 please'.
    """
    m = re.search(r'\bseason\s*(\d+)\b', text.strip(), flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Fall back to a bare leading number (e.g. "1 please")
    return _extract_number_from_message(text)


def _resolve_pending_numeric_selection(user_message: str, state: dict = None, user_info: dict = None) -> str | None:
    """Resolve numeric follow-ups for pending title disambiguation and series season selection."""
    if state is None:
        return None

    trimmed = (user_message or '').strip()

    # Pending movie disambiguation for add_radarr_movie flow.
    pending_movie = state.get('pending_movie_add')
    if pending_movie:
        index = _extract_number_from_message(trimmed)
        if index is not None:
            options = pending_movie.get('options', [])
            if index < 1 or index > len(options):
                return f"Please choose a number between 1 and {len(options)}."
            picked = options[index - 1]
            state.pop('pending_movie_add', None)
            return add_radarr_movie_handler(
                pending_movie.get('query', picked.get('title', '')),
                state=state,
                preferred_tmdb_id=picked.get('tmdbId'),
                preferred_year=picked.get('year'),
                user_info=user_info,
                is_kids=pending_movie.get('is_kids'),
            )

    # Pending kids/adults check for add_radarr_movie flow.
    pending_kids = state.get('pending_kids_check')
    if pending_kids:
        lower = trimmed.lower()
        has_kids = re.search(r'\bkids?\b', lower) is not None
        has_adults = re.search(r'\badults?\b', lower) is not None
        # Treat explicit negation like "not kids" / "no kids" as an adults preference.
        negated_kids = re.search(r'\b(?:no|not)\s+kids?\b', lower) is not None

        if has_kids and has_adults:
            # Both keywords mentioned — ambiguous; ask again.
            if pending_kids.get('kind') == 'series':
                return "Your reply mentioned both **kids** and **adults**. Please reply with only **kids** or only **adults** to confirm if this show should go to kids TV or adults TV."
            return "Your reply mentioned both **kids** and **adults**. Please reply with only **kids** or only **adults** to confirm the movie category."
        elif negated_kids and not has_adults:
            is_kids = False
        elif has_adults and not has_kids:
            is_kids = False
        elif has_kids and not has_adults:
            is_kids = True
        else:
            if pending_kids.get('kind') == 'series':
                return "Please reply with **kids** or **adults** to confirm if this show should go to kids TV or adults TV."
            return "Please reply with **kids** or **adults** to confirm the movie category."
        state.pop('pending_kids_check', None)
        if pending_kids.get('kind') == 'series':
            return add_sonarr_series_handler(
                pending_kids.get('query', ''),
                season=pending_kids.get('season'),
                state=state,
                preferred_tvdb_id=pending_kids.get('tvdbId'),
                preferred_year=pending_kids.get('year'),
                user_info=user_info,
                is_kids=is_kids,
            )
        return add_radarr_movie_handler(
            pending_kids.get('query', ''),
            state=state,
            preferred_tmdb_id=pending_kids.get('tmdbId'),
            preferred_year=pending_kids.get('year'),
            user_info=user_info,
            is_kids=is_kids,
        )

    # Pending series disambiguation for add_sonarr_series flow.
    pending_series_pick = state.get('pending_series_pick')
    if pending_series_pick:
        index = _extract_number_from_message(trimmed)
        if index is not None:
            options = pending_series_pick.get('options', [])
            if index < 1 or index > len(options):
                return f"Please choose a number between 1 and {len(options)}."
            picked = options[index - 1]
            state.pop('pending_series_pick', None)
            return add_sonarr_series_handler(
                pending_series_pick.get('query', picked.get('title', '')),
                state=state,
                preferred_tvdb_id=picked.get('tvdbId'),
                preferred_year=picked.get('year'),
                user_info=user_info,
                is_kids=pending_series_pick.get('is_kids'),
            )

    # Pending season selection for add_sonarr_series flow.
    pending_series = state.get('pending_series_add')
    if pending_series:
        season = _extract_season_from_message(trimmed)
        if season is not None:
            available = pending_series.get('available_seasons', [])
            if available and season not in available:
                return (
                    f"Please choose one of the available seasons for '{pending_series.get('title', 'that series')}': "
                    f"{', '.join(str(n) for n in available)}."
                )
            return add_sonarr_series_handler(
                pending_series.get('title', ''),
                season=season,
                state=state,
                preferred_tvdb_id=pending_series.get('tvdbId'),
                preferred_year=pending_series.get('year'),
                user_info=user_info,
                is_kids=pending_series.get('is_kids'),
            )

    pending = state.get('pending_title_lookup')
    if not pending:
        return None
    index = _extract_number_from_message(trimmed)
    if index is None:
        return None
    options = pending.get('options', [])
    if index < 1 or index > len(options):
        return f"Please choose a number between 1 and {len(options)}."

    picked = options[index - 1]
    results = credit_cache.search_title_credits(
        picked['title'],
        media_type=picked.get('media_type'),
        role=pending.get('role'),
    )
    if results is None:
        return "The credit search index is still being built. Please try again in a moment."

    # Narrow to the exact selected title/type/year.
    filtered = [
        r for r in results
        if r.get('title') == picked.get('title')
        and r.get('media_type') == picked.get('media_type')
        and r.get('year') == picked.get('year')
    ]

    state.pop('pending_title_lookup', None)
    return _format_title_credits_results(
        filtered,
        title=picked.get('title'),
        role=pending.get('role'),
        state=state,
    )


def delete_movie_handler(title: str, delete_files: bool = True, user_info: dict = None) -> str:
    if not _is_owner(user_info):
        return "❌ Sorry, only the server owner can delete media."

    radarr = RadarrAPI()
    matches = radarr.find_movie_in_library(title)
    if not matches:
        return f"No movie matching '{title}' found in your library."

    if len(matches) > 1:
        titles = ", ".join(
            f"{m['title']} ({m.get('year', '?')})" for m in matches[:5]
        )
        return f"Multiple movies found matching '{title}': {titles}. Please be more specific."

    movie = matches[0]
    success = radarr.delete_movie(movie['id'], delete_files=delete_files)
    if success:
        return f"✅ '{movie['title']} ({movie.get('year', '')})' has been removed from your library."
    return f"Failed to delete '{movie['title']}'. Please check Radarr."


def delete_tv_series_handler(title: str, season: int = None, delete_files: bool = True, user_info: dict = None) -> str:
    if not _is_owner(user_info):
        return "❌ Sorry, only the server owner can delete media."

    sonarr = SonarrAPI()
    matches = sonarr.find_series_in_library(title)
    if not matches:
        return f"No TV series matching '{title}' found in your library."

    if len(matches) > 1:
        titles = ", ".join(
            f"{s['title']} ({s.get('year', '?')})" for s in matches[:5]
        )
        return f"Multiple series found matching '{title}': {titles}. Please be more specific."

    series = matches[0]

    # Season-specific deletion: unmonitor the season and optionally delete its files
    if season is not None:
        all_seasons = series.get('seasons', [])
        real_seasons = [s for s in all_seasons if s['seasonNumber'] > 0]
        season_exists = any(s['seasonNumber'] == season for s in real_seasons)
        if not season_exists:
            valid = sorted(s['seasonNumber'] for s in real_seasons)
            return (
                f"Season {season} doesn't exist for '{series['title']}'. "
                f"Available seasons: {', '.join(str(n) for n in valid)}."
            )

        unmonitored = sonarr.unmonitor_season(series['id'], season)
        if not unmonitored:
            return f"Failed to unmonitor Season {season} of '{series['title']}'. Please check Sonarr."

        files_deleted = False
        if delete_files:
            episode_files = sonarr.get_episode_files(series['id'], season_number=season)
            if episode_files is None:
                # API error fetching files
                files_deleted = False
            elif not episode_files:
                # No files on disk — nothing to delete, treat as success
                files_deleted = True
            else:
                file_ids = [f['id'] for f in episode_files]
                files_deleted = sonarr.delete_episode_files_bulk(file_ids)

        if delete_files and not files_deleted:
            return (
                f"✅ Season {season} of '{series['title']}' has been unmonitored, "
                f"but its files could not be deleted. Please check Sonarr."
            )
        return (
            f"✅ Season {season} of '{series['title']}' has been unmonitored and "
            f"{'its files deleted from disk' if delete_files else 'kept on disk'}."
        )

    # Full series deletion
    success = sonarr.delete_series(series['id'], delete_files=delete_files)
    if success:
        return f"✅ '{series['title']} ({series.get('year', '')})' has been removed from your library."
    return f"Failed to delete '{series['title']}'. Please check Sonarr."


def recommend_similar_handler(title: str, media_type: str = None, state: dict = None) -> str:
    """Return library titles that share directors or cast with the given reference title."""
    # Always search credits without media_type filter so we can find the reference title
    # regardless of type, then apply media_type only when searching for related titles.
    credits = credit_cache.search_title_credits(title, media_type=None)
    if credits is None:
        return "The credit search index is still being built. Please try again in a moment."
    if not credits:
        return (
            f"Sorry, I couldn't find '{title}' in the library, so I can't suggest similar titles. "
            "If you'd like to add it, just ask!"
        )

    # Collect unique directors and top-billed actors for the reference title.
    directors = list(dict.fromkeys(c['person_name'] for c in credits if c['role'] == 'director'))
    actors = list(dict.fromkeys(c['person_name'] for c in credits if c['role'] == 'actor'))

    # Normalise the reference title for exclusion comparison.
    ref_title_lower = credits[0]['title'].lower()
    ref_display = credits[0]['title']

    # Search the library for every known person and collect matching titles.
    # Limit to the top 5 actors to keep results focused and avoid overly broad matches.
    seen_keys: set = set()
    recommendations: list = []

    for person in directors + actors[:5]:
        results = credit_cache.search(person, media_type=media_type) or []
        for r in results:
            if r['title'].lower() == ref_title_lower:
                continue
            key = (r['title'].lower(), r['media_type'])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            recommendations.append(r)

    if not recommendations:
        return (
            f"I found '{ref_display}' in the library but couldn't find other titles "
            "sharing its cast or directors. Try browsing by actor or director directly!"
        )

    # Sort by year descending so newest appear first.
    recommendations.sort(key=lambda r: r.get('year') or 0, reverse=True)

    lines = [f"Here are titles in your library that share cast or directors with '{ref_display}':"]
    for r in recommendations[:15]:
        label = "📺" if r['media_type'] == 'tv' else "🎬"
        status = "downloaded" if r['hasFile'] else "monitored"
        lines.append(f"  {label} {r['title']} ({r.get('year', '?')}) [{status}]")
    if len(recommendations) > 15:
        lines.append(f"  …and {len(recommendations) - 15} more.")
    return "\n".join(lines)


def _parse_tool_call_from_text(text):
    """Fallback: parse a tool call if the model outputs it as raw JSON text."""
    VALID_TOOLS = {
        'add_radarr_movie', 'add_sonarr_series', 'search_by_person', 'search_title_credits',
        'delete_movie', 'delete_tv_series', 'recommend_similar',
    }
    match = re.search(r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*(\{[^}]+\})\s*\}', text)
    if not match:
        return None
    name = match.group(1)
    if name not in VALID_TOOLS:
        return None
    try:
        args = json.loads(match.group(2))
    except json.JSONDecodeError:
        return None
    # Return an object that looks like a tool_call
    class FakeToolCall:
        def __init__(self, fn_name, fn_args):
            self.function = type('F', (), {'name': fn_name, 'arguments': json.dumps(fn_args)})()
    return FakeToolCall(name, args)


def _normalize_title_phrase(text: str) -> str:
    cleaned = text.strip().rstrip('?.! ')
    cleaned = re.sub(r'^(the\s+)?(movie|film|show|tv show|tv series|series)\s+', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


_DECADE_MAP = {
    'eighties': (1980, 1989), '80s': (1980, 1989), '80\'s': (1980, 1989),
    'nineties': (1990, 1999), '90s': (1990, 1999), '90\'s': (1990, 1999),
    'seventies': (1970, 1979), '70s': (1970, 1979), '70\'s': (1970, 1979),
    'sixties': (1960, 1969), '60s': (1960, 1969), '60\'s': (1960, 1969),
    'two thousands': (2000, 2009), '2000s': (2000, 2009),
    'two thousands ten': (2010, 2019), '2010s': (2010, 2019), 'tens': (2010, 2019),
}


def _detect_decade(text: str):
    """Return (start_year, end_year) if a decade is mentioned, else None."""
    lowered = text.lower()
    # Match e.g. "in the 80s", "around the 80's", "1980s"
    m = re.search(r'\b(19[0-9]0s|20[012][0-9]0s)\b', lowered)
    if m:
        start = int(m.group(1)[:4])
        return (start, start + 9)
    for key, span in _DECADE_MAP.items():
        if key in lowered:
            return span
    return None


def _infer_media_type_from_query(text: str) -> str | None:
    """Infer media type intent from user wording.

    Returns:
        'movie' when query clearly asks for movies only
        'tv' when query clearly asks for TV/shows only
        None when query is generic and should include both
    """
    lowered = (text or '').lower()

    asks_movies = bool(re.search(r'\b(movie|movies|film|films)\b', lowered))
    asks_tv = bool(re.search(r'\b(tv|television|show|shows|series)\b', lowered))

    if asks_movies and not asks_tv:
        return 'movie'
    if asks_tv and not asks_movies:
        return 'tv'
    return None


def _normalize_short_plural_person_name(text: str) -> str:
    cleaned = (text or '').strip(" '\u2019").strip()
    if ' ' in cleaned:
        return cleaned
    if not re.fullmatch(r'[A-Za-z-]{4,5}', cleaned):
        return cleaned
    if not cleaned.lower().endswith('s'):
        return cleaned
    return cleaned[:-1]


def _capabilities_response() -> str:
    """Return a stable help message covering tested chat capabilities."""
    lines = [
        "Here’s what I can help with right now:",
        "",
        "1. Add a movie by title.",
        "2. Add a TV series and let you choose which season to grab.",
        "3. Send kids movies and kids TV to separate library folders when that’s what you want.",
        "4. Search your library by actor or director.",
        "5. Tell you who starred in or directed a specific movie or show in your library.",
        "6. Recommend movies or shows from your library that share cast or directors with something you already have.",
        "7. Delete movies or TV series if you’re the server owner.",
        "",
        "A few handy examples:",
        "- Add the movie Sinners",
        "- Add the show Adolescence",
        "- Add Bluey for kids",
        "- What movies do I have with Tom Hanks?",
        "- Who directed Goodfellas?",
        "- Who starred in Severance?",
        "- Recommend something like Interstellar",
        "- Delete the movie Jaws 3",
        "",
        "Everything I do stays within your Plex-connected library and services.",
    ]
    return "\n".join(lines)


# Fraction of non-whitespace characters that must be non-ASCII before a paragraph is
# considered a foreign-language preamble and discarded.  0.5 is intentionally permissive:
# legitimate English text with accented names (e.g. "François Truffaut") sits well below
# this threshold, while purely Cyrillic or CJK paragraphs land at or above it.
_NON_ENGLISH_PARA_THRESHOLD = 0.5


def _strip_non_english_preamble(text: str) -> str:
    """Strip leading paragraphs that are predominantly non-ASCII/non-English characters.

    Some LLM models (e.g. qwen2.5) occasionally emit a foreign-language preamble
    before the actual English response. This helper removes those leading segments
    and also strips spurious language-tag artifacts of the form ``word English: …``
    that precede the real content.
    """
    # Split on two or more consecutive newlines so that triple-newline gaps are also
    # treated as paragraph boundaries (matching the '\n\n'.join reconstruction below).
    paragraphs = re.split(r'\n\n+', text)
    result: list[str] = []
    found_english = False

    for para in paragraphs:
        if found_english:
            result.append(para)
            continue

        content = para.strip()
        if not content:
            continue

        non_ws = [c for c in content if not c.isspace()]
        if not non_ws:
            continue

        non_ascii_ratio = sum(1 for c in non_ws if ord(c) > 127) / len(non_ws)
        if non_ascii_ratio > _NON_ENGLISH_PARA_THRESHOLD:
            # This paragraph is predominantly non-ASCII; treat it as a foreign-language
            # preamble and discard it.
            continue

        # Strip a leading language-tag artifact such as "widaemsag English:".
        # The pattern matches an arbitrary non-whitespace token (the garbled word the
        # model emits as a language marker) followed by the literal word "English:".
        # We keep this intentionally broad so it catches whatever token the model uses.
        content = re.sub(r'^\s*\S+\s+English:\s*', '', content, flags=re.IGNORECASE)

        found_english = True
        result.append(content)

    return '\n\n'.join(result)


def _sanitize_direct_response_text(text: str) -> tuple[str, bool]:
    """Remove accidental JSON code blocks and non-English preambles from user-facing direct responses."""
    if not isinstance(text, str):
        return "", False

    original = text
    # Strip fenced JSON blocks that sometimes leak from model reasoning/tool-like output.
    sanitized = re.sub(r"```json\s*.*?```", "", original, flags=re.IGNORECASE | re.DOTALL)
    # If the model used generic fences for JSON-like output, remove those too.
    sanitized = re.sub(r"```\s*\{\s*\"status\".*?```", "", sanitized, flags=re.IGNORECASE | re.DOTALL)
    # Strip non-English preambles (e.g. Cyrillic/CJK paragraphs before the English response).
    sanitized = _strip_non_english_preamble(sanitized)
    # Normalize blank lines after block removal.
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()

    return sanitized or original, sanitized != original


def _try_rule_based_route(user_message: str, state: dict = None, telemetry: dict = None) -> str | None:
    normalized_message = (user_message or '').strip()
    normalized_casefold = normalized_message.casefold()

    help_patterns = [
        re.compile(r'^help\??$', re.IGNORECASE),
        re.compile(r'^what\s+can\s+you\s+do\??$', re.IGNORECASE),
        re.compile(r'^what\s+do\s+you\s+do\??$', re.IGNORECASE),
        re.compile(r'^show\s+help\??$', re.IGNORECASE),
        re.compile(r'^(?:list|show)\s+(?:your\s+)?(?:features|capabilities|commands)\??$', re.IGNORECASE),
    ]
    for pattern in help_patterns:
        if pattern.match(normalized_message):
            if telemetry is not None:
                telemetry['heuristic_route'] = 'capabilities_help'
            return _capabilities_response()

    # --- Download queue/status lookups ---
    download_status_patterns = (
        re.compile(r'\bdownload(?:ing)?\s+(?:status|progress)\b'),
        re.compile(r'\bqueue\s+status\b'),
        re.compile(r'\b(?:status|progress)\s+of\s+(?:the\s+)?(?:download|queue)\b'),
        re.compile(r'\bdownload\s+is\s+(?:done|ready|complete|completed|finished)\b'),
        re.compile(r'\bqueue\s+is\s+(?:done|ready|complete|completed|finished)\b'),
        re.compile(r'\b(?:done|ready|complete|completed|finished)\s+downloading\b'),
        re.compile(r'\bwhen\b.*\b(?:complete|completed|finish|finished)\b.*\bdownload(?:ing)?\b'),
    )
    if any(pattern.search(normalized_casefold) for pattern in download_status_patterns):
        if telemetry is not None:
            telemetry['heuristic_route'] = 'check_download_status'
        return check_download_status_handler()

    # --- Title credit lookups (who directed/starred in a specific title) ---
    director_prefix = "who directed "
    if normalized_casefold.startswith(director_prefix):
        raw_phrase = normalized_message[len(director_prefix):].strip()
        title = _normalize_title_phrase(raw_phrase)
        if title:
            if telemetry is not None:
                telemetry['heuristic_route'] = 'search_title_credits:director'
            media_type = _infer_media_type_from_query(raw_phrase)
            return search_title_credits_handler(title, role='director', state=state, media_type=media_type)

    actor_prefixes = ("who starred in ", "who stars in ", "who acted in ", "who acts in ", "who is in ")
    for prefix in actor_prefixes:
        if normalized_casefold.startswith(prefix):
            raw_phrase = normalized_message[len(prefix):].strip()
            title = _normalize_title_phrase(raw_phrase)
            if title:
                if telemetry is not None:
                    telemetry['heuristic_route'] = 'search_title_credits:actor'
                media_type = _infer_media_type_from_query(raw_phrase)
                return search_title_credits_handler(title, role='actor', state=state, media_type=media_type)
            break

    # --- Person filmography lookups (what does X star in / list all Xs) ---
    # Use separate non-greedy patterns so the name doesn't swallow the trailing verb.
    _PERSON_PATTERNS = [
        # "what does/has/did [name] star/starred in"
        (re.compile(r'^what\s+(?:has|did|does)\s+(.+?)\s+(?:star(?:red|s)?(?:\s+in)?|been\s+in|appear(?:ed|s)?(?:\s+in)?|acted\s+in)\??$', re.IGNORECASE), None, False),
        # "list/show/find all [name]s" or "list [name]"
        (re.compile(r'^(?:list|show|find)\s+(?:all\s+|me\s+all\s+)?(.+?)(?:[\'\'s]*)?\??$', re.IGNORECASE), None, False),
        # "movies with [name]" / "movies starring [name]"
        (re.compile(r'^(?:what\s+)?movies?\s+(?:with|starring)\s+(.+?)\??$', re.IGNORECASE), 'movie', False),
        # "shows with [name]" / "shows starring [name]"
        (re.compile(r'^(?:what\s+)?(?:tv\s+)?shows?\s+(?:with|starring)\s+(.+?)\??$', re.IGNORECASE), 'tv', False),
        # "any tv series with [name] starring" / "any tv shows with [name]"
        (re.compile(r'^any\s+(?:tv\s+series|(?:tv\s+)?shows?)\s+(?:with\s+)?(.+?)(?:\s+star(?:ring|ing)|\s+staring)?\??$', re.IGNORECASE), 'tv', False),
        # "any [name] in tv series" / "any [name] staring in tv series"
        (re.compile(r'^any\s+(.+?)\s+(?:(?:star(?:ring|ing)|staring)\s+)?in\s+(?:tv\s+series|(?:tv\s+)?shows?)\??$', re.IGNORECASE), 'tv', True),
    ]
    for pat, media_type, normalize_short_plural in _PERSON_PATTERNS:
        pm = pat.match(normalized_message)
        if pm:
            name = pm.group(1).strip(" '\u2019s").strip()
            if normalize_short_plural:
                name = _normalize_short_plural_person_name(name)
            # Guard: skip if the extracted name looks like a command keyword rather than a real name
            if name and len(name) >= 2 and not re.search(
                r'^\s*(?:add|delete|remove|find|search|list|show|movies?|shows?|series|all)\s*$',
                name, re.IGNORECASE
            ):
                if telemetry is not None:
                    telemetry['heuristic_route'] = 'search_by_person:actor'
                return search_by_person_handler(name, media_type=media_type, role='actor', state=state)

    # --- Decade follow-up: "around in the 80s" / "in the 90s" when we have a recent person search ---
    decade = _detect_decade(normalized_message)
    if decade and state is not None:
        last = state.get('last_person_search')
        if last:
            start_yr, end_yr = decade
            filtered = [m for m in last['results'] if m.get('year') and start_yr <= m['year'] <= end_yr]
            if not filtered:
                return (
                    f"No titles from the {decade[0]}s found for '{last['query']}' in your library. "
                    f"Try a different decade or check the full list above."
                )
            distinct_filtered = list(dict.fromkeys(m['person_name'] for m in filtered))
            if len(distinct_filtered) == 1:
                # Decade narrowed it to one person
                name = distinct_filtered[0]
                state['last_person_search']['results'] = filtered
                state['last_person_search']['distinct_names'] = distinct_filtered
                if telemetry is not None:
                    telemetry['heuristic_route'] = 'decade_filter'
                return _format_person_results(filtered, name)
            else:
                # Still multiple — show filtered grouped list
                lines = [f"People matching '{last['query']}' with titles from the {start_yr}s–{end_yr}s:"]
                for full_name in distinct_filtered:
                    person_results = [m for m in filtered if m['person_name'] == full_name]
                    lines.append(f"\n\u2022 {full_name} ({len(person_results)} title{'s' if len(person_results) != 1 else ''} in that decade):")
                    for m in person_results:
                        label = "📺" if m['media_type'] == 'tv' else "🎬"
                        lines.append(f"    {label} {m['title']} ({m['year']})")
                lines.append(f"\nTry using a full name, e.g. \"what has {distinct_filtered[0]} starred in\".")
                if telemetry is not None:
                    telemetry['heuristic_route'] = 'decade_filter_ambiguous'
                return "\n".join(lines)

    # --- Similarity / recommendation queries ---
    _SIMILAR_PATTERNS = [
        # "movies/shows similar to X" / "films similar to X"
        re.compile(r'^(?:what\s+)?(?:movies?|films?|shows?|series|tv\s+shows?)\s+(?:are\s+)?similar\s+to\s+(.+?)\??$', re.IGNORECASE),
        # "movies/films like X"
        re.compile(r'^(?:what\s+)?(?:movies?|films?|shows?|series|tv\s+shows?)\s+(?:are\s+)?like\s+(.+?)\??$', re.IGNORECASE),
        # "recommend ??? similar to X" / "recommend something like X"
        re.compile(r'^recommend\s+(?:\w+\s+)*similar\s+to\s+(.+?)\??$', re.IGNORECASE),
        re.compile(r'^recommend\s+(?:\w+\s+)*like\s+(.+?)\??$', re.IGNORECASE),
        # "what is similar to X" / "what's similar to X"
        re.compile(r"^what(?:'s|\s+is)\s+similar\s+to\s+(.+?)\??$", re.IGNORECASE),
        # "anything similar to X" / "anything like X"
        re.compile(r'^anything\s+(?:similar\s+to|like)\s+(.+?)\??$', re.IGNORECASE),
        # "suggestions similar to X" / "suggestions like X"
        re.compile(r'^(?:any\s+)?suggestions?\s+(?:similar\s+to|like)\s+(.+?)\??$', re.IGNORECASE),
    ]
    for pat in _SIMILAR_PATTERNS:
        sm = pat.match(normalized_message)
        if sm:
            raw_phrase = sm.group(1)
            ref_title = _normalize_title_phrase(raw_phrase)
            if ref_title:
                if telemetry is not None:
                    telemetry['heuristic_route'] = 'recommend_similar'
                media_type = _infer_media_type_from_query(normalized_message)
                return recommend_similar_handler(ref_title, media_type=media_type, state=state)

    return None


def chat_with_llm(
    user_message: str,
    user_info: dict = None,
    state: dict = None,
    request_id: str = None,
    telemetry: dict = None,
    prior_turns: list = None,
) -> str:
    """Send a user message to the NeMo Claw model and handle tool calls.
    
    Args:
        user_message: The current user's message
        user_info: User information dict (contains plex_user if session-authenticated)
        state: Mutable workflow state dict for tracking pending selections/tasks
        request_id: Request correlation ID for observability
        telemetry: Mutable dict for capturing metrics (llm_duration_ms, tool_calls, etc.)
        prior_turns: List of prior conversation turns (dicts with role/content keys)
    """
    if not client:
        return "AI Client is not initialized."

    def _prepare_prior_turn_messages(turns: list | None) -> list[dict]:
        if not turns:
            return []

        prepared_turns = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue

            role = turn.get("role")
            content = turn.get("content")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue

            prepared_turns.append({"role": role, "content": content})

        if config.CONVERSATION_MEMORY_MAX_TURNS > 0:
            prepared_turns = prepared_turns[-config.CONVERSATION_MEMORY_MAX_TURNS :]

        return prepared_turns

    prepared_prior_turns = _prepare_prior_turn_messages(prior_turns)

    telemetry = telemetry if telemetry is not None else {}
    telemetry.update({
        'model': config.OLLAMA_MODEL,
        'fallback_tool_parser': False,
        'tool_calls': [],
        'numeric_selection': False,
        'request_id': request_id,
        'prior_turn_count_used': len(prepared_prior_turns),
    })

    numeric_selection_result = _resolve_pending_numeric_selection(user_message, state=state, user_info=user_info)
    if numeric_selection_result is not None:
        telemetry['numeric_selection'] = True
        return numeric_selection_result

    heuristic_result = _try_rule_based_route(user_message, state=state, telemetry=telemetry)
    if heuristic_result is not None:
        return heuristic_result

    # Build system prompt.

    messages = [
        {
            "role": "system",
            "content": (
                "LANGUAGE REQUIREMENT: Always respond exclusively in English. Never respond in other "
                "languages. If unsure, default to English and provide clear, direct answers.\n\n"
                "You are a media library assistant. You can add movies (via Radarr), add TV series "
                "(via Sonarr), search the library by actor/actress or director name, look up who "
                "starred in or directed a given movie/TV title, delete movies or TV series "
                "(owner only), and check download progress.\n\n"
                "KIDS/ADULTS CLASSIFICATION RULES:\n"
                "- When the user says FOR KIDS, FOR CHILDREN, FOR FAMILY, or implies kids content → set is_kids=true.\n"
                "- When the user says FOR ADULTS, MATURE, or implies adult content → set is_kids=false.\n"
                "- When unclear: if the title is known to be kid-friendly (animations, family films) → is_kids=true; "
                "if known to be adult-oriented → is_kids=false; if completely ambiguous → omit the parameter and "
                "the system will auto-classify, then prompt the user to confirm their preference (KIDS or ADULTS).\n"
                "- CRITICAL: Never guess. If the user's intent is ambiguous, always omit is_kids and let the system "
                "classification + user confirmation handle it. The confirmation prompt is safe and correct.\n\n"
                "- Media defaults (such as availability, quality profiles, and series type) are enforced "
                "automatically according to server configuration; do not assume or describe them as fixed values.\n"
                "- If per-user daily limits are enabled, they are enforced automatically according to server "
                "configuration. You may mention that limits exist, but do not state specific numbers unless "
                "explicitly provided in the conversation.\n\n"
                "TOOL ROUTING RULES (priority order — execute the first matching rule):\n"
                "1. ADD MOVIE: User says ADD/GET/ADD TO LIBRARY with a MOVIE title → call add_radarr_movie immediately.\n"
                "2. ADD TV SERIES: User says ADD/GET/ADD TO LIBRARY with a TV/SHOW/SERIES title → call add_sonarr_series. "
                "If season specified, include it. If NOT specified, call WITHOUT season first (shows available seasons), "
                "then ask user which season they want.\n"
                "3. SEASON SELECTION: User replies with just a season number (1, 2, 3, etc.) after you showed available seasons "
                "→ call add_sonarr_series again WITH the season parameter.\n"
                "4. SEARCH BY PERSON (ACTOR): User asks 'who starred in', 'what with', 'movies/shows with [NAME]' "
                "→ call search_by_person with role='actor' IMMEDIATELY. Never ask for clarification first; the tool handles "
                "partial names automatically and will group results by full name if needed. "
                "Set media_type='movie' for movies only, 'tv' for TV only, or omit for both.\n"
                "5. SEARCH BY PERSON (DIRECTOR): User asks 'who directed', 'what [DIRECTOR] directed' "
                "→ call search_by_person with role='director' IMMEDIATELY. Never ask for clarification first.\n"
                "6. LIST ALL PEOPLE: User asks to 'list all' people with a given name → call search_by_person "
                "with that name; the tool will show all matching people grouped by full name.\n"
                "7. SEARCH TITLE CREDITS: User asks 'WHO STARRED IN [TITLE]', 'WHO DIRECTED [TITLE]', 'CAST OF [TITLE]' "
                "→ call search_title_credits with title set to that title. Set role='actor' for cast, role='director' for directors.\n"
                "8. NUMERIC SELECTION: User replies with a single digit (1, 2, 3) when you've offered multiple matches "
                "→ treat as selection of that option.\n"
                "9. DELETE MOVIE: User says DELETE/REMOVE with a MOVIE title → call delete_movie.\n"
                "10. DELETE TV SERIES: User says DELETE/REMOVE with a TV/SERIES/SEASON title → call delete_tv_series. "
                "Include the season number in the 'season' parameter when a specific season is mentioned "
                "(e.g., 'delete Lost season 2'). NEVER call add_sonarr_series for a delete/remove request.\n"
                "11. RECOMMENDATIONS: User asks 'LIKE [TITLE]', 'SIMILAR TO [TITLE]', or 'RECOMMEND' based on a title "
                "→ call recommend_similar with the reference title. "
                "Set media_type='movie' for movie-only requests, 'tv' for TV-only, or omit for both.\n"
                "12. DOWNLOAD STATUS: User asks 'IS IT READY', 'IS IT DONE', 'DOWNLOAD STATUS', 'DOWNLOAD PROGRESS', "
                "or about download ISSUES or ERRORS → call check_download_status.\n"
                "13. GENERAL QUESTIONS: Any other question → respond directly WITHOUT calling tools.\n\n"
                "RESPONSE FORMAT:\n"
                "- Keep responses brief and direct. No unnecessary explanations.\n"
                "- When presenting multiple options (titles, seasons, people), use numbered lists (1., 2., 3., ...).\n"
                "- When confirming an action, state it clearly (e.g., 'Adding Inception to movies...').\n"
                "- Always respond in English only. Verify your response is in English before sending."
            )
        },
    ]
    
    # Inject sanitized bounded conversation history before the current message.
    messages.extend(prepared_prior_turns)
    
    # Add current user message
    messages.append({"role": "user", "content": user_message})

    try:
        llm_started = time.perf_counter()
        with start_span('llm.chat_completion', {'model': config.OLLAMA_MODEL}):
            response = client.chat.completions.create(
                model=config.OLLAMA_MODEL,
                messages=messages,
                tools=tools
            )
        telemetry['llm_duration_ms'] = round((time.perf_counter() - llm_started) * 1000, 2)
        
        response_message = response.choices[0].message
        
        # Check if the LLM decided to call a function
        tool_calls = response_message.tool_calls
        
        # Fallback: sometimes the model outputs tool calls as raw text instead of structured tool_calls
        if not tool_calls and response_message.content:
            parsed = _parse_tool_call_from_text(response_message.content)
            if parsed:
                tool_calls = [parsed]
                telemetry['fallback_tool_parser'] = True

        if tool_calls:
            results = []
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                safe_arguments = redact_sensitive_fields(arguments)
                tool_started = time.perf_counter()
                
                log.info(
                    'llm.tool_call',
                    extra={
                        'tool_name': function_name,
                        'tool_arguments': safe_arguments,
                        'request_id': request_id,
                    },
                )
                
                with start_span('llm.tool_execution', {'tool.name': function_name}):
                    if function_name == "add_radarr_movie":
                        result = add_radarr_movie_handler(
                            arguments.get("title"),
                            state=state,
                            user_info=user_info,
                            is_kids=arguments.get("is_kids"),
                        )
                    elif function_name == "add_sonarr_series":
                        result = add_sonarr_series_handler(
                            arguments.get("title"),
                            season=arguments.get("season"),
                            state=state,
                            user_info=user_info,
                            is_kids=arguments.get("is_kids"),
                        )
                    elif function_name == "search_by_person":
                        requested_media_type = arguments.get("media_type")
                        inferred_media_type = _infer_media_type_from_query(user_message)
                        # If user did not explicitly ask for only movies or only TV,
                        # search both by forcing media_type=None.
                        effective_media_type = inferred_media_type if inferred_media_type is not None else requested_media_type
                        if inferred_media_type is None:
                            effective_media_type = None

                        result = search_by_person_handler(
                            arguments.get("person_name"),
                            state=state,
                            media_type=effective_media_type,
                            role=arguments.get("role"),
                        )
                    elif function_name == "search_title_credits":
                        result = search_title_credits_handler(
                            arguments.get("title"),
                            media_type=arguments.get("media_type"),
                            role=arguments.get("role"),
                            state=state,
                        )
                    elif function_name == "delete_movie":
                        result = delete_movie_handler(
                            arguments.get("title"),
                            delete_files=arguments.get("delete_files", True),
                            user_info=user_info,
                        )
                    elif function_name == "delete_tv_series":
                        result = delete_tv_series_handler(
                            arguments.get("title"),
                            season=arguments.get("season"),
                            delete_files=arguments.get("delete_files", True),
                            user_info=user_info,
                        )
                    elif function_name == "recommend_similar":
                        result = recommend_similar_handler(
                            arguments.get("title"),
                            media_type=arguments.get("media_type"),
                            state=state,
                        )
                    elif function_name == "check_download_status":
                        result = check_download_status_handler()
                    else:
                        result = f"Unknown function: {function_name}"

                duration_ms = round((time.perf_counter() - tool_started) * 1000, 2)
                telemetry['tool_calls'].append({
                    'name': function_name,
                    'arguments': safe_arguments,
                    'duration_ms': duration_ms,
                })
                log.info(
                    'llm.tool_result',
                    extra={
                        'tool_name': function_name,
                        'duration_ms': duration_ms,
                        'result_preview': result[:200],
                        'request_id': request_id,
                    },
                )
                results.append(result)
            
            # Return the handler results directly - they're already human-readable
            final_result = "\n".join(results)
            return final_result
        else:
            telemetry['direct_response'] = True
            safe_response, changed = _sanitize_direct_response_text(response_message.content)
            telemetry['direct_response_sanitized'] = changed
            return safe_response

    except Exception:
        log.exception("Error in chat_with_llm", extra={'request_id': request_id})
        return "Something went wrong while processing your request. Please try again."
