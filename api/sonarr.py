import requests
import os

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

    def add_series(self, series, root_folder_path, quality_profile_id, season_number=None):
        """Adds a series to Sonarr. If season_number is given, only that season is monitored.
        Returns (result_dict, None) on success, (None, error_message) on failure."""
        series['rootFolderPath'] = root_folder_path
        series['monitored'] = True
        series['qualityProfileId'] = quality_profile_id

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
