import json
import re
from openai import OpenAI
from api.radarr import RadarrAPI, credit_cache
from api.sonarr import SonarrAPI

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
            "name": "search_movies_by_actor",
            "description": "Searches the user's movie library for films starring a given actor or actress. Returns which movies they have with that actor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actor_name": {
                        "type": "string",
                        "description": "The name of the actor or actress to search for."
                    }
                },
                "required": ["actor_name"]
            }
        }
    }
]

def add_radarr_movie_handler(title: str) -> str:
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

def search_movies_by_actor_handler(actor_name: str) -> str:
    results = credit_cache.search(actor_name)
    if results is None:
        return "The actor search index is still being built. Please try again in a moment."
    if not results:
        return f"No movies starring '{actor_name}' found in your library."
    
    lines = [f"Movies starring '{actor_name}' in your library ({len(results)}):"]
    for m in results:
        status = "downloaded" if m['hasFile'] else "monitored"
        lines.append(f"  - {m['title']} ({m['year']}) as {m['character']} [{status}]")
    return "\n".join(lines)


def _parse_tool_call_from_text(text):
    """Fallback: parse a tool call if the model outputs it as raw JSON text."""
    VALID_TOOLS = {'add_radarr_movie', 'add_sonarr_series', 'search_movies_by_actor'}
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


def chat_with_llm(user_message: str) -> str:
    """Send a user message to the NeMo Claw model and handle tool calls."""
    if not client:
        return "AI Client is not initialized."

    messages = [
        {
            "role": "system",
            "content": (
                "You are a media library assistant. You can add movies (via Radarr), add TV series (via Sonarr), "
                "and search the movie library by actor/actress name.\n"
                "RULES:\n"
                "- When the user asks to ADD a MOVIE, call add_radarr_movie.\n"
                "- When the user asks to ADD a TV SERIES or TV SHOW, call add_sonarr_series. If the user specifies a season number, include it. If they don't specify a season, call it WITHOUT the season parameter first to see available seasons, then tell the user which seasons are available and ask them which one they'd like.\n"
                "- When the user replies with a season number for a show you already looked up, call add_sonarr_series again WITH the season parameter.\n"
                "- When the user asks what MOVIES star a particular ACTOR or ACTRESS, or what films by an actor are in the library, call search_movies_by_actor.\n"
                "- For general questions, respond directly without calling any tools.\n"
                "- Be concise."
            )
        },
        {"role": "user", "content": user_message}
    ]

    try:
        # Assuming the model name 'nemotron' or similar, though litellm/v1 often tolerates any string.
        # Check actual model name if an error occurs. Let's use 'default' for now or just generic.
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
                elif function_name == "search_movies_by_actor":
                    result = search_movies_by_actor_handler(arguments.get("actor_name"))
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
