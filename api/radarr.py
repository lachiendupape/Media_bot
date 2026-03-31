import difflib
import os
import requests
import sqlite3
import threading
import time

from config import RADARR_URL, RADARR_API_KEY
from api.plex import PlexAPI

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

    def get_root_folders(self):
        """Gets all root folders from Radarr."""
        try:
            return self._get('/api/v3/rootfolder')
        except requests.exceptions.RequestException as e:
            print(f"Error getting root folders from Radarr: {e}")
            return []

    def get_root_folder_by_path(self, preferred_path):
        """Return the root folder matching *preferred_path*, else first available."""
        roots = self.get_root_folders()
        if not roots:
            return None
        if preferred_path:
            target = preferred_path.strip().lower().rstrip('/')
            for folder in roots:
                path = str(folder.get('path', '')).strip().lower().rstrip('/')
                if path == target:
                    return folder
        return roots[0]

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

    def get_quality_profile_by_name(self, profile_name):
        """Return a quality profile matching *profile_name*, else first available."""
        profiles = self.get_quality_profiles() or []
        if not profiles:
            return None
        if profile_name:
            wanted = profile_name.strip().lower()
            for profile in profiles:
                if str(profile.get('name', '')).strip().lower() == wanted:
                    return profile
            for profile in profiles:
                if wanted in str(profile.get('name', '')).strip().lower():
                    return profile
        return profiles[0]

    def _get_tag_id(self, label):
        """Get or create a Radarr tag ID by label."""
        if not label or not str(label).strip():
            return None
        try:
            existing = self._get('/api/v3/tag')
            for tag in existing:
                if str(tag.get('label', '')).strip().lower() == label.strip().lower():
                    return tag.get('id')
            response = self._post('/api/v3/tag', {'label': label})
            response.raise_for_status()
            created = response.json()
            return created.get('id')
        except requests.exceptions.RequestException as e:
            print(f"Error getting/creating Radarr tag '{label}': {e}")
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

    def add_movie(
        self,
        movie,
        root_folder_path,
        quality_profile_id,
        minimum_availability='released',
        tags=None,
    ):
        """Adds a movie to Radarr. Returns (result_dict, None) on success,
        (None, error_message) on failure."""
        movie = dict(movie)
        movie['rootFolderPath'] = root_folder_path
        movie['qualityProfileId'] = quality_profile_id
        movie['monitored'] = True
        movie['minimumAvailability'] = minimum_availability
        if tags:
            tag_ids = [self._get_tag_id(tag) for tag in tags]
            movie['tags'] = [tag_id for tag_id in tag_ids if tag_id is not None]
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

    @property
    def entry_count(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT COUNT(*) FROM credits').fetchone()
            return int(row[0]) if row else 0

    def build(self):
        """Build the credit cache from Radarr and Sonarr APIs. Call from a background thread."""
        if self._building:
            return
        with self._lock:
            self._building = True
        print("[CreditCache] Building credit cache (movies + TV)...", flush=True)
        try:
            radarr = RadarrAPI()
            plex = PlexAPI()
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
            tv_rows = []
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
                            tv_rows.append((
                                name,
                                series.get('title', '?'),
                                series.get('year'),
                                c.get('character', '?'),
                                has_file,
                                'tv',
                                'actor',
                            ))
                        elif credit_type == 'crew' and c.get('job', '').lower() == 'director':
                            tv_rows.append((
                                name,
                                series.get('title', '?'),
                                series.get('year'),
                                'Director',
                                has_file,
                                'tv',
                                'director',
                            ))
                if tv_rows:
                    rows.extend(tv_rows)
                elif plex.configured:
                    print("[CreditCache] Sonarr credits unavailable; falling back to Plex TV metadata.", flush=True)
                    plex_show_count = 0
                    for section in plex.get_show_sections():
                        shows = plex.get_section_shows(section['key'])
                        for show in shows:
                            plex_show_count += 1
                            if plex_show_count % 50 == 0:
                                print(f"[CreditCache] Plex TV metadata: {plex_show_count} titles", flush=True)
                            for c in plex.get_show_credits(show['ratingKey']):
                                name = c.get('personName', '').lower().strip()
                                if not name:
                                    continue
                                rows.append((
                                    name,
                                    show.get('title', '?'),
                                    show.get('year'),
                                    c.get('character') or '?',
                                    1,
                                    'tv',
                                    'actor',
                                ))
                    print(f"[CreditCache] Plex TV cast done: {plex_show_count} titles.", flush=True)
                else:
                    print("[CreditCache] Sonarr credits unavailable and PLEX_TOKEN not configured; TV cast search will be empty.", flush=True)
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

            # Fall back to fuzzy name match (handles minor typos/variations)
            if not rows:
                extra_conditions = []
                extra_params = []
                if media_type:
                    extra_conditions.append('media_type = ?')
                    extra_params.append(media_type)
                if role:
                    extra_conditions.append('role = ?')
                    extra_params.append(role)
                where_extra = (' AND ' + ' AND '.join(extra_conditions)) if extra_conditions else ''
                name_rows = conn.execute(
                    f'SELECT DISTINCT person_name FROM credits WHERE 1=1{where_extra}', extra_params
                ).fetchall()
                all_names = [r[0] for r in name_rows]
                close_matches = difflib.get_close_matches(query, all_names, n=5, cutoff=0.6)
                if close_matches:
                    placeholders = ','.join('?' * len(close_matches))
                    rows = conn.execute(
                        f'SELECT * FROM credits WHERE person_name IN ({placeholders}){where_extra}'
                        f' ORDER BY year DESC',
                        close_matches + extra_params,
                    ).fetchall()

            return [
                {
                    'title': r['title'],
                    'year': r['year'],
                    'person_name': r['person_name'],
                    'character': r['character'],
                    'hasFile': bool(r['has_file']),
                    'media_type': r['media_type'],
                    'role': r['role'],
                }
                for r in rows
            ]

    def search_title_credits(self, title, media_type=None, role=None):
        """Search for people credits by media title. Returns list of dicts.

        Args:
            title: Movie or TV title to search.
            media_type: 'movie', 'tv', or None for all.
            role: 'actor', 'director', or None for all.
        """
        if not self.ready:
            return None
        query = title.lower().strip()

        conditions = ['lower(title) = ?']
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
                f'SELECT * FROM credits WHERE {where} ORDER BY year DESC, role, person_name', params
            ).fetchall()

            # Fall back to partial title match if exact match found nothing
            if not rows:
                conditions[0] = 'lower(title) LIKE ?'
                params[0] = f'%{query}%'
                where = ' AND '.join(conditions)
                rows = conn.execute(
                    f'SELECT * FROM credits WHERE {where} ORDER BY year DESC, role, person_name', params
                ).fetchall()

            # Fall back to fuzzy title match (handles punctuation differences like colons)
            if not rows:
                extra_conditions = []
                extra_params = []
                if media_type:
                    extra_conditions.append('media_type = ?')
                    extra_params.append(media_type)
                if role:
                    extra_conditions.append('role = ?')
                    extra_params.append(role)
                where_extra = (' AND ' + ' AND '.join(extra_conditions)) if extra_conditions else ''
                title_rows = conn.execute(
                    f'SELECT DISTINCT lower(title) FROM credits WHERE 1=1{where_extra}', extra_params
                ).fetchall()
                all_titles = [r[0] for r in title_rows]
                close_matches = difflib.get_close_matches(query, all_titles, n=5, cutoff=0.6)
                if close_matches:
                    placeholders = ','.join('?' * len(close_matches))
                    rows = conn.execute(
                        f'SELECT * FROM credits WHERE lower(title) IN ({placeholders}){where_extra}'
                        f' ORDER BY year DESC, role, person_name',
                        close_matches + extra_params,
                    ).fetchall()

            return [
                {
                    'title': r['title'],
                    'year': r['year'],
                    'person_name': r['person_name'],
                    'character': r['character'],
                    'hasFile': bool(r['has_file']),
                    'media_type': r['media_type'],
                    'role': r['role'],
                }
                for r in rows
            ]


# Singleton cache instance
credit_cache = RadarrCreditCache()

