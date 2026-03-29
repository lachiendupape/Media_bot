import requests
import sys
import os
import sqlite3
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import RADARR_URL, RADARR_API_KEY

# Shared session for connection pooling (reuses TCP+SSL connections)
_session = requests.Session()
_TIMEOUT = 30

# SQLite DB path (next to this file)
_DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'credit_cache.db')


class RadarrAPI:
    def __init__(self):
        self.base_url = RADARR_URL
        self.api_key = RADARR_API_KEY

    def _get(self, path, params=None, timeout=_TIMEOUT):
        url = f"{self.base_url}{path}"
        p = {'apiKey': self.api_key}
        if params:
            p.update(params)
        response = _session.get(url, params=p, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _post(self, path, json_data, timeout=_TIMEOUT):
        url = f"{self.base_url}{path}"
        response = _session.post(url, json=json_data, params={'apiKey': self.api_key}, timeout=timeout)
        return response

    def get_system_status(self):
        """Gets the system status from Radarr."""
        try:
            return self._get('/api/v3/system/status')
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to Radarr: {e}")
            return None

    def get_root_folder(self):
        """Gets the root folder from Radarr."""
        try:
            return self._get('/api/v3/rootfolder')[0]
        except (requests.exceptions.RequestException, IndexError) as e:
            print(f"Error getting root folder from Radarr: {e}")
            return None

    def lookup_movie(self, term):
        """Looks up a movie by search term in Radarr."""
        try:
            return self._get('/api/v3/movie/lookup', params={'term': term})
        except requests.exceptions.RequestException as e:
            print(f"Error looking up movie in Radarr: {e}")
            return None

    def get_quality_profiles(self):
        """Gets the quality profiles from Radarr."""
        try:
            return self._get('/api/v3/qualityprofile')
        except requests.exceptions.RequestException as e:
            print(f"Error getting quality profiles from Radarr: {e}")
            return None

    def get_library_movies(self):
        """Gets all movies in the Radarr library."""
        try:
            return self._get('/api/v3/movie', timeout=120)
        except requests.exceptions.RequestException as e:
            print(f"Error getting library from Radarr: {e}")
            return None

    def get_movie_credits(self, movie_id):
        """Gets credits/cast for a specific movie."""
        try:
            return self._get('/api/v3/credit', params={'movieId': movie_id})
        except requests.exceptions.RequestException as e:
            return []

    def add_movie(self, movie, root_folder_path, quality_profile_id):
        """Adds a movie to Radarr. Returns (result_dict, None) on success,
        (None, error_message) on failure."""
        movie['rootFolderPath'] = root_folder_path
        movie['qualityProfileId'] = quality_profile_id
        movie['monitored'] = True
        movie['addOptions'] = {'searchForMovie': True}

        try:
            response = self._post('/api/v3/movie', movie)
            response.raise_for_status()
            return response.json(), None
        except requests.exceptions.HTTPError as e:
            try:
                error_body = e.response.json()
                error_msg = str(error_body)
            except Exception:
                error_msg = e.response.text
            print(f"Radarr add error ({e.response.status_code}): {error_msg}")
            if e.response.status_code == 400 and 'already been added' in error_msg.lower():
                return None, 'already_exists'
            return None, error_msg
        except requests.exceptions.RequestException as e:
            print(f"Error adding movie to Radarr: {e}")
            return None, str(e)


class RadarrCreditCache:
    """SQLite-backed cache of actor->movie mappings. Persists across restarts."""

    def __init__(self, db_path=_DB_PATH):
        self.db_path = os.path.abspath(db_path)
        self._building = False
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS credits (
                actor TEXT NOT NULL,
                title TEXT NOT NULL,
                year INTEGER,
                character TEXT,
                has_file INTEGER
            )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_actor ON credits(actor)')
            conn.execute('''CREATE TABLE IF NOT EXISTS cache_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )''')

    @property
    def ready(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM cache_meta WHERE key='last_built'").fetchone()
            return row is not None

    @property
    def age_seconds(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM cache_meta WHERE key='last_built'").fetchone()
            if not row:
                return float('inf')
            return time.time() - float(row[0])

    def build(self):
        """Build the credit cache from Radarr API. Call from a background thread."""
        if self._building:
            return
        with self._lock:
            self._building = True
        print("[CreditCache] Building actor cache...", flush=True)
        try:
            radarr = RadarrAPI()
            movies = radarr.get_library_movies()
            if not movies:
                print("[CreditCache] Failed to fetch library.", flush=True)
                return

            rows = []
            for i, movie in enumerate(movies):
                if (i + 1) % 50 == 0:
                    print(f"[CreditCache] Progress: {i+1}/{len(movies)}", flush=True)
                credits = radarr.get_movie_credits(movie['id'])
                for c in credits:
                    if c.get('type') != 'cast':
                        continue
                    name = c.get('personName', '').lower().strip()
                    if not name:
                        continue
                    rows.append((
                        name,
                        movie.get('title', '?'),
                        movie.get('year'),
                        c.get('character', '?'),
                        1 if movie.get('hasFile', False) else 0,
                    ))

            with sqlite3.connect(self.db_path) as conn:
                conn.execute('DELETE FROM credits')
                conn.executemany('INSERT INTO credits VALUES (?,?,?,?,?)', rows)
                conn.execute("INSERT OR REPLACE INTO cache_meta VALUES ('last_built', ?)",
                             (str(time.time()),))
                conn.execute("INSERT OR REPLACE INTO cache_meta VALUES ('movie_count', ?)",
                             (str(len(movies)),))

            print(f"[CreditCache] Ready: {len(rows)} credits from {len(movies)} movies.", flush=True)
        except Exception as e:
            print(f"[CreditCache] Error building cache: {e}", flush=True)
        finally:
            with self._lock:
                self._building = False

    def search(self, actor_name):
        """Search for movies by actor name. Returns list of dicts."""
        if not self.ready:
            return None
        query = actor_name.lower().strip()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Exact match first
            rows = conn.execute(
                'SELECT * FROM credits WHERE actor = ? ORDER BY year DESC', (query,)
            ).fetchall()
            # Fall back to partial match
            if not rows:
                rows = conn.execute(
                    'SELECT * FROM credits WHERE actor LIKE ? ORDER BY year DESC',
                    (f'%{query}%',)
                ).fetchall()
            return [
                {
                    'title': r['title'],
                    'year': r['year'],
                    'character': r['character'],
                    'hasFile': bool(r['has_file']),
                }
                for r in rows
            ]


# Singleton cache instance
credit_cache = RadarrCreditCache()
