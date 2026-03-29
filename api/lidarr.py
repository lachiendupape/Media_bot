import requests
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import LIDARR_URL, LIDARR_API_KEY


class LidarrAPI:
    def __init__(self):
        self.base_url = LIDARR_URL
        self.api_key = LIDARR_API_KEY

    def get_system_status(self):
        """Gets the system status from Lidarr."""
        url = f"{self.base_url}/api/v1/system/status?apiKey={self.api_key}"
        try:
            response = requests.get(url)
            response.raise_for_status()  # Raise an exception for bad status codes
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to Lidarr: {e}")
            return None
