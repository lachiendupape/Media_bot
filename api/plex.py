import xml.etree.ElementTree as ET

import requests

from config import PLEX_APP_NAME, PLEX_CLIENT_ID, PLEX_SERVER_URL, PLEX_TOKEN

_session = requests.Session()
_TIMEOUT = 30

_PLEX_HEADERS = {
    'Accept': 'application/xml',
    'X-Plex-Product': PLEX_APP_NAME,
    'X-Plex-Client-Identifier': PLEX_CLIENT_ID,
}


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class PlexAPI:
    def __init__(self):
        self.base_url = (PLEX_SERVER_URL or '').rstrip('/')
        self.token = PLEX_TOKEN

    @property
    def configured(self):
        return bool(self.base_url and self.token)

    def _get_xml(self, path, params=None, timeout=_TIMEOUT):
        if not self.configured:
            return None
        query = {'X-Plex-Token': self.token}
        if params:
            query.update(params)
        response = _session.get(f"{self.base_url}{path}", headers=_PLEX_HEADERS, params=query, timeout=timeout)
        response.raise_for_status()
        return ET.fromstring(response.text)

    def get_show_sections(self):
        root = self._get_xml('/library/sections')
        if root is None:
            return []
        sections = []
        for directory in root.findall('.//Directory'):
            if directory.attrib.get('type') != 'show':
                continue
            key = directory.attrib.get('key')
            if not key:
                continue
            sections.append({
                'key': key,
                'title': directory.attrib.get('title', 'TV Shows'),
            })
        return sections

    def get_section_shows(self, section_key):
        root = self._get_xml(f'/library/sections/{section_key}/all', params={'type': '2'}, timeout=120)
        if root is None:
            return []
        shows = []
        for directory in root.findall('.//Directory'):
            rating_key = directory.attrib.get('ratingKey')
            if not rating_key:
                continue
            shows.append({
                'ratingKey': rating_key,
                'title': directory.attrib.get('title', '?'),
                'year': _to_int(directory.attrib.get('year')),
            })
        return shows

    def get_show_credits(self, rating_key):
        root = self._get_xml(f'/library/metadata/{rating_key}')
        if root is None:
            return []
        container = root.find('.//Directory') or root.find('.//Video')
        if container is None:
            return []

        credits = []
        for role in container.findall('Role'):
            name = (role.attrib.get('tag') or '').strip()
            if not name:
                continue
            credits.append({
                'personName': name,
                'character': (role.attrib.get('role') or '').strip(),
            })
        return credits