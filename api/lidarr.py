import requests
import os

from config import LIDARR_URL, LIDARR_API_KEY

_session = requests.Session()
_TIMEOUT = 30


class LidarrAPI:
    def __init__(self):
        self.base_url = LIDARR_URL
        self.api_key = LIDARR_API_KEY

    def _get(self, path, params=None, timeout=_TIMEOUT):
        url = f"{self.base_url}{path}"
        p = {'apiKey': self.api_key}
        if params:
            p.update(params)
        response = _session.get(url, params=p, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def get_system_status(self):
        """Gets the system status from Lidarr."""
        try:
            return self._get('/api/v1/system/status')
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to Lidarr: {e}")
            return None
