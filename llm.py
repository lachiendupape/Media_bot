import json
import logging
import re
import time
from openai import OpenAI
from api.radarr import RadarrAPI, credit_cache
from api.sonarr import SonarrAPI
import config
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
            "description": "Removes a TV series from the Sonarr library. Only the server owner may call this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The title of the TV series to delete."
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
]


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
    """Return True if user_info belongs to the configured server owner."""
    owner = config.OWNER_PLEX_USERNAME.strip().lower()
    if not owner:
        return False
    if not user_info:
        return False
    return user_info.get('username', '').lower() == owner


def add_radarr_movie_handler(title: str) -> str:
    ok, msg = _check_disk_space()
    if not ok:
        return msg

    radarr = RadarrAPI()
    movies = radarr.lookup_movie(title)
    if not movies:
        return f"Could not find any movies matching '{title}'."
    
    selected_movie = movies[0]
    root_folder = radarr.get_root_folder()
    if not root_folder:
        return "Failed to retrieve Radarr root folder."

    quality_profiles = radarr.get_quality_profiles()
    if not quality_profiles:
        return "Failed to retrieve Radarr quality profiles."

    quality_profile_id = quality_profiles[0]['id']
    result, error = radarr.add_movie(selected_movie, root_folder['path'], quality_profile_id)
    if result:
        return f"Great news! '{selected_movie['title']} ({selected_movie.get('year', '')})' has been grabbed and is downloading now — it'll be with you shortly!"
    if error == 'already_exists':
        return f"'{selected_movie['title']} ({selected_movie.get('year', '')})' is already in your library — no need to add it again!"
    return f"Failed to add movie '{selected_movie['title']}': {error}"

def add_sonarr_series_handler(title: str, season: int = None) -> str:
    ok, msg = _check_disk_space()
    if not ok:
        return msg

    sonarr = SonarrAPI()
    series = sonarr.lookup_series(title)
    if not series:
        return f"Could not find any series matching '{title}'."
        
    selected_series = series[0]
    seasons = [s for s in selected_series.get('seasons', []) if s['seasonNumber'] > 0]

    # No season specified — list available seasons and ask
    if season is None:
        if not seasons:
            return f"'{selected_series['title']}' has no season information available."
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

    root_folder = sonarr.get_root_folder()
    if not root_folder:
        return "Failed to retrieve Sonarr root folder."
        
    quality_profiles = sonarr.get_quality_profiles()
    if not quality_profiles:
        return "Failed to retrieve Sonarr quality profiles."
    
    quality_profile_id = quality_profiles[0]['id']
    result, error = sonarr.add_series(selected_series, root_folder['path'], quality_profile_id, season_number=season)
    
    if result:
        return f"Great news! '{selected_series['title']}' Season {season} has been grabbed and is downloading now — it'll be with you shortly!"
    if error == 'already_exists':
        return f"'{selected_series['title']} ({selected_series.get('year', '')})' is already in your library — no need to add it again!"
    return f"Failed to add TV series '{selected_series['title']}': {error}"

def search_by_person_handler(person_name: str, media_type: str = None, role: str = None) -> str:
    results = credit_cache.search(person_name, media_type=media_type, role=role)
    if results is None:
        return "The credit search index is still being built. Please try again in a moment."
    if not results:
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

    if multiple_people:
        # Group results by full name so user can identify who they meant
        lines = [f"Found {len(distinct_names)} people matching '{person_name}' — please use a full name to narrow down:"]
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
        lines.append(f"\nTry asking e.g. \"what has {distinct_names[0]} starred in\" for a specific person.")
        return "\n".join(lines)

    # Single person — normal output using their full name from results
    matched_name = distinct_names[0]
    lines = [f"Results for '{matched_name}' ({len(results)} title{'s' if len(results) != 1 else ''}):"]
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
                lines.append("Directors: " + ", ".join(d.title() for d in directors))
            else:
                lines.append("Directors: none found")

        if role in (None, 'actor'):
            if actors:
                lines.append("Cast: " + ", ".join(a.title() for a in actors[:20]))
                if len(actors) > 20:
                    lines.append(f"...and {len(actors) - 20} more")
            else:
                lines.append("Cast: none found")

        lines.append("")

    return "\n".join(lines).strip()


def _resolve_pending_numeric_selection(user_message: str, state: dict = None) -> str | None:
    """Resolve numeric replies (e.g. '2') against stored disambiguation options."""
    if state is None:
        return None
    pending = state.get('pending_title_lookup')
    if not pending:
        return None

    trimmed = (user_message or '').strip()
    if not trimmed.isdigit():
        return None

    index = int(trimmed)
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


def delete_tv_series_handler(title: str, delete_files: bool = True, user_info: dict = None) -> str:
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
    success = sonarr.delete_series(series['id'], delete_files=delete_files)
    if success:
        return f"✅ '{series['title']} ({series.get('year', '')})' has been removed from your library."
    return f"Failed to delete '{series['title']}'. Please check Sonarr."


def _parse_tool_call_from_text(text):
    """Fallback: parse a tool call if the model outputs it as raw JSON text."""
    VALID_TOOLS = {
        'add_radarr_movie', 'add_sonarr_series', 'search_by_person', 'search_title_credits',
        'delete_movie', 'delete_tv_series',
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


def _try_rule_based_route(user_message: str, state: dict = None, telemetry: dict = None) -> str | None:
    lowered = (user_message or '').strip()

    director_match = re.match(r'^who\s+directed\s+(.+)$', lowered, flags=re.IGNORECASE)
    if director_match:
        title = _normalize_title_phrase(director_match.group(1))
        if title:
            if telemetry is not None:
                telemetry['heuristic_route'] = 'search_title_credits:director'
            return search_title_credits_handler(title, role='director', state=state)

    actor_match = re.match(r'^who\s+(?:starred|stars|acted|acts|is)\s+in\s+(.+)$', lowered, flags=re.IGNORECASE)
    if actor_match:
        title = _normalize_title_phrase(actor_match.group(1))
        if title:
            if telemetry is not None:
                telemetry['heuristic_route'] = 'search_title_credits:actor'
            return search_title_credits_handler(title, role='actor', state=state)

    return None


def chat_with_llm(
    user_message: str,
    user_info: dict = None,
    state: dict = None,
    request_id: str = None,
    telemetry: dict = None,
) -> str:
    """Send a user message to the NeMo Claw model and handle tool calls."""
    if not client:
        return "AI Client is not initialized."

    telemetry = telemetry if telemetry is not None else {}
    telemetry.update({
        'model': config.OLLAMA_MODEL,
        'fallback_tool_parser': False,
        'tool_calls': [],
        'numeric_selection': False,
        'request_id': request_id,
    })

    numeric_selection_result = _resolve_pending_numeric_selection(user_message, state=state)
    if numeric_selection_result is not None:
        telemetry['numeric_selection'] = True
        return numeric_selection_result

    heuristic_result = _try_rule_based_route(user_message, state=state, telemetry=telemetry)
    if heuristic_result is not None:
        return heuristic_result

    messages = [
        {
            "role": "system",
            "content": (
                "You are a media library assistant. You can add movies (via Radarr), add TV series "
                "(via Sonarr), search the library by actor/actress or director name, look up who "
                "starred in or directed a given movie/TV title, and delete movies or TV series "
                "(owner only).\n"
                "RULES:\n"
                "- When the user asks to ADD a MOVIE, call add_radarr_movie.\n"
                "- When the user asks to ADD a TV SERIES or TV SHOW, call add_sonarr_series. "
                "If the user specifies a season number, include it. If they don't specify a season, "
                "call it WITHOUT the season parameter first to see available seasons, then tell the "
                "user which seasons are available and ask them which one they'd like.\n"
                "- When the user replies with a season number for a show you already looked up, "
                "call add_sonarr_series again WITH the season parameter.\n"
                "- When the user asks what movies or shows star a particular ACTOR or ACTRESS, "
                "call search_by_person with role='actor'. Set media_type='movie' for movies only, "
                "'tv' for TV only, or omit for both.\n"
                "- When the user asks what movies or shows a DIRECTOR directed, call search_by_person "
                "with role='director'. Set media_type='movie' or 'tv' as appropriate.\n"
                "- When the user asks WHO STARRED IN, WHO ACTS IN, WHO IS IN, or WHO DIRECTED a "
                "specific movie or TV title, call search_title_credits with title set to that title. "
                "Set role='actor' for cast questions and role='director' for director questions.\n"
                "- If you ask the user to choose between multiple title matches, and they reply with "
                "just a number (like 1, 2, or 3), treat that as their selection.\n"
                "- When the user asks to DELETE or REMOVE a MOVIE, call delete_movie.\n"
                "- When the user asks to DELETE or REMOVE a TV SERIES or TV SHOW, call delete_tv_series.\n"
                "- For general questions, respond directly without calling any tools.\n"
                "- Be concise."
            )
        },
        {"role": "user", "content": user_message}
    ]

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
                        result = add_radarr_movie_handler(arguments.get("title"))
                    elif function_name == "add_sonarr_series":
                        result = add_sonarr_series_handler(arguments.get("title"), season=arguments.get("season"))
                    elif function_name == "search_by_person":
                        result = search_by_person_handler(
                            arguments.get("person_name"),
                            media_type=arguments.get("media_type"),
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
                            delete_files=arguments.get("delete_files", True),
                            user_info=user_info,
                        )
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
            return "\n".join(results)
        else:
            telemetry['direct_response'] = True
            return response_message.content

    except Exception:
        log.exception("Error in chat_with_llm", extra={'request_id': request_id})
        return "Something went wrong while processing your request. Please try again."

