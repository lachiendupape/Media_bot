"""Plex OAuth authentication — PIN-based forwarding flow."""

import requests
from urllib.parse import urlencode
from config import PLEX_CLIENT_ID, PLEX_APP_NAME, PLEX_MACHINE_ID

PLEX_HEADERS = {
    'Accept': 'application/json',
    'X-Plex-Product': PLEX_APP_NAME,
    'X-Plex-Client-Identifier': PLEX_CLIENT_ID,
}


def create_pin():
    """Create a Plex PIN for OAuth. Returns (pin_id, pin_code)."""
    r = requests.post(
        'https://plex.tv/api/v2/pins',
        headers=PLEX_HEADERS,
        data={'strong': 'true'},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return data['id'], data['code']


def build_auth_url(pin_code, forward_url):
    """Build the Plex OAuth URL the user should be redirected to."""
    params = urlencode({
        'clientID': PLEX_CLIENT_ID,
        'code': pin_code,
        'forwardUrl': forward_url,
        'context[device][product]': PLEX_APP_NAME,
    })
    return f'https://app.plex.tv/auth#?{params}'


def check_pin(pin_id):
    """Check a PIN and return the auth token, or None if not yet claimed."""
    r = requests.get(
        f'https://plex.tv/api/v2/pins/{pin_id}',
        headers=PLEX_HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get('authToken')


def get_plex_user(auth_token):
    """Get user info from a Plex auth token. Returns dict with id, username, thumb."""
    r = requests.get(
        'https://plex.tv/api/v2/user',
        headers={**PLEX_HEADERS, 'X-Plex-Token': auth_token},
        timeout=10,
    )
    if r.status_code == 401:
        return None
    r.raise_for_status()
    data = r.json()
    return {
        'id': data.get('id'),
        'username': data.get('username') or data.get('title', 'Unknown'),
        'email': data.get('email', ''),
        'thumb': data.get('thumb', ''),
    }


def user_has_server_access(auth_token):
    """Check if the user's Plex account can see our server (is an invited friend or owner).

    Returns a tuple (has_access: bool, is_owner: bool).
    """
    r = requests.get(
        'https://plex.tv/api/v2/resources',
        headers={**PLEX_HEADERS, 'X-Plex-Token': auth_token},
        params={'includeHttps': '1', 'includeRelay': '0'},
        timeout=10,
    )
    r.raise_for_status()
    for resource in r.json():
        if resource.get('clientIdentifier') == PLEX_MACHINE_ID:
            return True, bool(resource.get('owned'))
    return False, False
