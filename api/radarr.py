import requests
import os
import sqlite3
import threading
import time

from config import RADARR_URL, RADARR_API_KEY

# Shared session for connection pooling (reuses TCP+SSL connections)
_session = requests.Session()
_TIMEOUT = 30

# SQLite DB path — uses DATA_DIR env var (for Docker volume) or project root
_DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(__file__), '..'))
_DB_PATH = os.path.join(_DATA_DIR, 'credit_cache.db')


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

    def _delete(self, path, params=None, timeout=_TIMEOUT):
        url = f"{self.base_url}{path}"
        p = {'apiKey': self.api_key}
        if params:
            p.update(params)
        response = _session.delete(url, params=p, timeout=timeout)
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

    def get_disk_space(self):
        """Gets disk space info for all mounts from Radarr."""
        try:
            return self._get('/api/v3/diskspace')
        except requests.exceptions.RequestException as e:
            print(f"Error getting disk space from Radarr: {e}")
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
            print(f"Warning: could not fetch credits for movie {movie_id}: {e}", flush=True)
            return []

    def find_movie_in_library(self, title):
        """Find movies in the library matching a title (case-insensitive)."""
        try:
            movies = self._get('/api/v3/movie', timeout=120)
            title_lower = title.lower()
            return [m for m in movies if title_lower in m.get('title', '').lower()]
        except requests.exceptions.RequestException as e:
            print(f"Error finding movie in library: {e}")
            return []

    def delete_movie(self, movie_id, delete_files=False):
        """Delete a movie from Radarr. Returns True on success."""
        try:
            response = self._delete(
                f'/api/v3/movie/{movie_id}',
                params={
                    'deleteFiles': str(delete_files).lower(),
                    'addImportExclusion': 'false',
                }
            )
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error deleting movie from Radarr: {e}")
            return False

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
    """SQLite-backed cache of person->media mappings. Covers movie/TV actors and directors."""

    def __init__(self, db_path=_DB_PATH):
        self.db_path = os.path.abspath(db_path)
        self._building = False
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            # Detect old schema (actor column) and recreate with new schema
            existing_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(credits)").fetchall()
            }
            if existing_cols and 'person_name' not in existing_cols:
                # Old schema detected (uses 'actor' column). Drop and recreate.
                # The cache is fully rebuild-able, so losing old rows is intentional.
                conn.execute('DROP TABLE IF EXISTS credits')
                existing_cols = set()
            if not existing_cols:
                conn.execute('''CREATE TABLE credits (
                    person_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    year INTEGER,
                    character TEXT,
                    has_file INTEGER,
                    media_type TEXT NOT NULL DEFAULT 'movie',
                    role TEXT NOT NULL DEFAULT 'actor'
                )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_person ON credits(person_name)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_media_type ON credits(media_type)')
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
        """Build the credit cache from Radarr and Sonarr APIs. Call from a background thread."""
        if self._building:
            return
        with self._lock:
            self._building = True
        print("[CreditCache] Building credit cache (movies + TV)...", flush=True)
        try:
            radarr = RadarrAPI()
            # Lazy import to avoid circular dependency at module level
            from api.sonarr import SonarrAPI
            sonarr = SonarrAPI()

            rows = []

            # --- Movies (actors + directors) ---
            movies = radarr.get_library_movies()
            if movies:
                for i, movie in enumerate(movies):
                    if (i + 1) % 50 == 0:
                        print(f"[CreditCache] Movies: {i+1}/{len(movies)}", flush=True)
                    has_file = 1 if movie.get('hasFile', False) else 0
                    for c in radarr.get_movie_credits(movie['id']):
                        credit_type = c.get('type', '').lower()
                        name = c.get('personName', '').lower().strip()
                        if not name:
                            continue
                        if credit_type == 'cast':
                            rows.append((
                                name,
                                movie.get('title', '?'),
                                movie.get('year'),
                                c.get('character', '?'),
                                has_file,
                                'movie',
                                'actor',
                            ))
                        elif credit_type == 'crew' and c.get('job', '').lower() == 'director':
                            rows.append((
                                name,
                                movie.get('title', '?'),
                                movie.get('year'),
                                'Director',
                                has_file,
                                'movie',
                                'director',
                            ))
                print(f"[CreditCache] Movies done: {len(movies)} titles.", flush=True)
            else:
                print("[CreditCache] No movies fetched from Radarr.", flush=True)

            # --- TV series (actors + directors) ---
            series_list = sonarr.get_library_series()
            if series_list:
                for i, series in enumerate(series_list):
                    if (i + 1) % 50 == 0:
                        print(f"[CreditCache] TV series: {i+1}/{len(series_list)}", flush=True)
                    stats = series.get('statistics', {})
                    has_file = 1 if stats.get('episodeFileCount', 0) > 0 else 0
                    for c in sonarr.get_series_credits(series['id']):
                        credit_type = c.get('type', '').lower()
                        name = c.get('personName', '').lower().strip()
                        if not name:
                            continue
                        if credit_type == 'cast':
                            rows.append((
                                name,
                                series.get('title', '?'),
                                series.get('year'),
                                c.get('character', '?'),
                                has_file,
                                'tv',
                                'actor',
                            ))
                        elif credit_type == 'crew' and c.get('job', '').lower() == 'director':
                            rows.append((
                                name,
                                series.get('title', '?'),
                                series.get('year'),
                                'Director',
                                has_file,
                                'tv',
                                'director',
                            ))
                print(f"[CreditCache] TV series done: {len(series_list)} titles.", flush=True)
            else:
                print("[CreditCache] No TV series fetched from Sonarr.", flush=True)

            total_media = (len(movies) if movies else 0) + (len(series_list) if series_list else 0)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('DELETE FROM credits')
                conn.executemany('INSERT INTO credits VALUES (?,?,?,?,?,?,?)', rows)
                conn.execute("INSERT OR REPLACE INTO cache_meta VALUES ('last_built', ?)",
                             (str(time.time()),))
                conn.execute("INSERT OR REPLACE INTO cache_meta VALUES ('movie_count', ?)",
                             (str(total_media),))

            print(f"[CreditCache] Ready: {len(rows)} credits from {total_media} titles.", flush=True)
        except Exception as e:
            print(f"[CreditCache] Error building cache: {e}", flush=True)
        finally:
            with self._lock:
                self._building = False

    def search(self, person_name, media_type=None, role=None):
        """Search for titles by person name. Returns list of dicts.

        Args:
            person_name: Actor or director name to search.
            media_type: 'movie', 'tv', or None for all.
            role: 'actor', 'director', or None for all.
        """
        if not self.ready:
            return None
        query = person_name.lower().strip()

        conditions = ['person_name = ?']
        params = [query]
        if media_type:
            conditions.append('media_type = ?')
            params.append(media_type)
        if role:
            conditions.append('role = ?')
            params.append(role)
        where = ' AND '.join(conditions)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'SELECT * FROM credits WHERE {where} ORDER BY year DESC', params
            ).fetchall()

            # Fall back to partial match if exact match found nothing
            if not rows:
                conditions[0] = 'person_name LIKE ?'
                params[0] = f'%{query}%'
                where = ' AND '.join(conditions)
                rows = conn.execute(
                    f'SELECT * FROM credits WHERE {where} ORDER BY year DESC', params
                ).fetchall()

            return [
                {
                    'title': r['title'],
                    'year': r['year'],
                    'character': r['character'],
                    'hasFile': bool(r['has_file']),
                    'media_type': r['media_type'],
                    'role': r['role'],
                }
                for r in rows
            ]


# Singleton cache instance
credit_cache = RadarrCreditCache()

