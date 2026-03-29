import json
import re
from openai import OpenAI
from api.radarr import RadarrAPI, credit_cache
from api.sonarr import SonarrAPI
import config

# Minimum free disk space percentage before blocking new downloads
_DISK_FREE_THRESHOLD = 0.05  # 5%

# Initialize OpenAI client pointed to local NeMo Claw inference API
# You can change this base_url if NeMo Claw is exposed on a different path.
try:
    client = OpenAI(
        base_url="http://127.0.0.1:11434/v1",
        api_key="ollama" # Some valid string is required by the SDK
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

    # Group output by media type for readability
    lines = [f"Results for '{person_name}' ({len(results)} title{'s' if len(results) != 1 else ''}):"]
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
        'add_radarr_movie', 'add_sonarr_series', 'search_by_person',
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


def chat_with_llm(user_message: str, user_info: dict = None) -> str:
    """Send a user message to the NeMo Claw model and handle tool calls."""
    if not client:
        return "AI Client is not initialized."

    messages = [
        {
            "role": "system",
            "content": (
                "You are a media library assistant. You can add movies (via Radarr), add TV series "
                "(via Sonarr), search the library by actor/actress or director name, and delete "
                "movies or TV series (owner only).\n"
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
                "- When the user asks to DELETE or REMOVE a MOVIE, call delete_movie.\n"
                "- When the user asks to DELETE or REMOVE a TV SERIES or TV SHOW, call delete_tv_series.\n"
                "- For general questions, respond directly without calling any tools.\n"
                "- Be concise."
            )
        },
        {"role": "user", "content": user_message}
    ]

    try:
        response = client.chat.completions.create(
            model="qwen2.5:7b", 
            messages=messages,
            tools=tools
        )
        
        response_message = response.choices[0].message
        
        # Check if the LLM decided to call a function
        tool_calls = response_message.tool_calls
        
        # Fallback: sometimes the model outputs tool calls as raw text instead of structured tool_calls
        if not tool_calls and response_message.content:
            parsed = _parse_tool_call_from_text(response_message.content)
            if parsed:
                tool_calls = [parsed]

        if tool_calls:
            results = []
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                
                print(f"LLM called tool: {function_name} with args: {arguments}", flush=True)
                
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
                
                print(f"Tool result: {result}", flush=True)
                results.append(result)
            
            # Return the handler results directly - they're already human-readable
            return "\n".join(results)
        else:
            return response_message.content

    except Exception as e:
        import traceback
        return f"Error communicating with AI: {str(e)}\n{traceback.format_exc()}"

