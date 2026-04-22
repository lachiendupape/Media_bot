import requests

from config import SONARR_URL, SONARR_API_KEY

_session = requests.Session()
_TIMEOUT = 30


class SonarrAPI:
    def __init__(self):
        self.base_url = SONARR_URL
        self.api_key = SONARR_API_KEY

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

    def _put(self, path, json_data, timeout=_TIMEOUT):
        url = f"{self.base_url}{path}"
        response = _session.put(url, json=json_data, params={'apiKey': self.api_key}, timeout=timeout)
        return response

    def _delete(self, path, params=None, json_data=None, timeout=_TIMEOUT):
        url = f"{self.base_url}{path}"
        p = {'apiKey': self.api_key}
        if params:
            p.update(params)
        response = _session.delete(url, params=p, json=json_data, timeout=timeout)
        return response

    def get_system_status(self):
        """Gets the system status from Sonarr."""
        try:
            return self._get('/api/v3/system/status')
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to Sonarr: {e}")
            return None

    def get_root_folder(self):
        """Gets the root folder from Sonarr."""
        try:
            return self._get('/api/v3/rootfolder')[0]
        except (requests.exceptions.RequestException, IndexError) as e:
            print(f"Error getting root folder from Sonarr: {e}")
            return None

    def get_root_folders(self):
        """Gets all root folders from Sonarr."""
        try:
            return self._get('/api/v3/rootfolder')
        except requests.exceptions.RequestException as e:
            print(f"Error getting root folders from Sonarr: {e}")
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
        """Gets disk space info for all mounts from Sonarr."""
        try:
            return self._get('/api/v3/diskspace')
        except requests.exceptions.RequestException as e:
            print(f"Error getting disk space from Sonarr: {e}")
            return None

    def get_queue(self):
        """Gets the active download/import queue from Sonarr."""
        try:
            return self._get('/api/v3/queue')
        except requests.exceptions.RequestException as e:
            print(f"Error getting queue from Sonarr: {e}")
            return None

    def lookup_series(self, term):
        """Looks up a series by search term in Sonarr."""
        try:
            return self._get('/api/v3/series/lookup', params={'term': term})
        except requests.exceptions.RequestException as e:
            print(f"Error looking up series in Sonarr: {e}")
            return None

    def get_quality_profiles(self):
        """Gets the quality profiles from Sonarr."""
        try:
            return self._get('/api/v3/qualityprofile')
        except requests.exceptions.RequestException as e:
            print(f"Error getting quality profiles from Sonarr: {e}")
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
        """Get or create a Sonarr tag ID by label."""
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
            print(f"Error getting/creating Sonarr tag '{label}': {e}")
            return None

    def get_library_series(self):
        """Gets all series in the Sonarr library."""
        try:
            return self._get('/api/v3/series', timeout=120)
        except requests.exceptions.RequestException as e:
            print(f"Error getting library from Sonarr: {e}")
            return None

    def get_series_credits(self, series_id):
        """Gets credits/cast for a specific series."""
        try:
            return self._get('/api/v3/credit', params={'seriesId': series_id})
        except requests.exceptions.RequestException:
            return []

    def find_series_in_library(self, title):
        """Find series in the library matching a title (case-insensitive)."""
        try:
            series_list = self._get('/api/v3/series', timeout=120)
            title_lower = title.lower()
            return [s for s in series_list if title_lower in s.get('title', '').lower()]
        except requests.exceptions.RequestException as e:
            print(f"Error finding series in library: {e}")
            return []

    def delete_series(self, series_id, delete_files=False):
        """Delete a series from Sonarr. Returns True on success."""
        try:
            response = self._delete(
                f'/api/v3/series/{series_id}',
                params={
                    'deleteFiles': str(delete_files).lower(),
                    'addImportListExclusion': 'false',
                }
            )
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error deleting series from Sonarr: {e}")
            return False

    def get_episode_files(self, series_id, season_number=None):
        """Get episode files for a series. Optionally filter by season_number. Returns list or None on error."""
        try:
            params = {'seriesId': series_id}
            if season_number is not None:
                params['seasonNumber'] = season_number
            return self._get('/api/v3/episodefile', params=params)
        except requests.exceptions.RequestException as e:
            print(f"Error getting episode files from Sonarr: {e}")
            return None

    def delete_episode_files_bulk(self, file_ids):
        """Delete multiple episode files by ID. Returns True on success."""
        try:
            response = self._delete('/api/v3/episodefile/bulk', json_data={'episodeFileIds': file_ids})
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error bulk-deleting episode files from Sonarr: {e}")
            return False

    def unmonitor_season(self, series_id, season_number):
        """Unmonitor a specific season for a series in Sonarr. Returns True on success."""
        try:
            series_data = self._get(f'/api/v3/series/{series_id}')
            for season in series_data.get('seasons', []):
                if season['seasonNumber'] == season_number:
                    season['monitored'] = False
                    break
            response = self._put(f'/api/v3/series/{series_id}', series_data)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error unmonitoring season in Sonarr: {e}")
            return False

    def get_series_by_tvdb_id(self, tvdb_id):
        """Find a series in the Sonarr library by its TVDB ID. Returns the series dict or None."""
        try:
            series_list = self._get('/api/v3/series', timeout=120)
            return next((s for s in series_list if s.get('tvdbId') == tvdb_id), None)
        except requests.exceptions.RequestException as e:
            print(f"Error getting series from Sonarr: {e}")
            return None

    def update_series(self, series_id, series_data):
        """Update an existing series in Sonarr. Returns (result_dict, None) on success."""
        try:
            response = self._put(f'/api/v3/series/{series_id}', series_data)
            response.raise_for_status()
            return response.json(), None
        except requests.exceptions.RequestException as e:
            print(f"Error updating series in Sonarr: {e}")
            return None, str(e)

    def search_season(self, series_id, season_number):
        """Trigger a season search command in Sonarr. Returns True on success."""
        try:
            response = self._post('/api/v3/command', {
                'name': 'SeasonSearch',
                'seriesId': series_id,
                'seasonNumber': season_number,
            })
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error triggering season search in Sonarr: {e}")
            return False

    def add_series(
        self,
        series,
        root_folder_path,
        quality_profile_id,
        season_number=None,
        series_type='standard',
        tags=None,
    ):
        """Adds a series to Sonarr. If season_number is given, only that season is monitored.
        Returns (result_dict, None) on success, (None, error_message) on failure."""
        series = dict(series)
        series['rootFolderPath'] = root_folder_path
        series['monitored'] = True
        series['qualityProfileId'] = quality_profile_id
        series['seriesType'] = series_type
        if tags:
            tag_ids = [self._get_tag_id(tag) for tag in tags]
            series['tags'] = [tag_id for tag_id in tag_ids if tag_id is not None]

        # Only monitor the requested season
        if season_number is not None:
            for s in series.get('seasons', []):
                s['monitored'] = (s['seasonNumber'] == season_number)
            series['addOptions'] = {'searchForMissingEpisodes': True}
        else:
            series['addOptions'] = {'searchForMissingEpisodes': True}

        try:
            response = self._post('/api/v3/series', series)
            response.raise_for_status()
            return response.json(), None
        except requests.exceptions.HTTPError as e:
            try:
                error_body = e.response.json()
                error_msg = str(error_body)
            except Exception:
                error_msg = e.response.text
            print(f"Sonarr add error ({e.response.status_code}): {error_msg}")
            if e.response.status_code == 400 and 'already been added' in error_msg.lower():
                return None, 'already_exists'
            return None, error_msg
        except requests.exceptions.RequestException as e:
            print(f"Error adding series to Sonarr: {e}")
            return None, str(e)

