#!/usr/bin/env python3
"""Free Deezer playlist backup (publicly accessible data only).

Usage:
  python deezer_playlist_export.py 12345678
  python deezer_playlist_export.py https://www.deezer.com/profile/12345678
  python deezer_playlist_export.py --playlist https://www.deezer.com/playlist/3155776842
  python deezer_playlist_export.py 12345678 --playlist 3155776842

No third-party dependencies, login, or developer API key required. Does not
retrieve private playlists, hidden liked tracks, or DRM-protected audio.
"""

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

API = 'https://api.deezer.com'
COLUMNS = (
    'playlist_id', 'playlist_title', 'position', 'track_id', 'title',
    'artist', 'album', 'duration_seconds', 'isrc', 'track_url',
    'artist_url', 'album_url',
)


def get_json(url):
    """Fetch public Deezer API JSON, with simple rate-limit retries."""
    parts = urlparse(url)
    if parts.scheme != 'https' or parts.hostname != 'api.deezer.com':
        raise ValueError(f'Refusing non-Deezer API URL: {url}')
    for attempt in range(4):
        try:
            req = Request(url, headers={'User-Agent': 'DeezerPlaylistBackup/1.0'})
            with urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
            if not isinstance(payload, dict):
                raise RuntimeError('Unexpected JSON response')
            if payload.get('error'):
                err = payload['error']
                raise RuntimeError(f'Deezer API error: {err}')
            return payload
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
            else:
                raise RuntimeError(f'HTTP {exc.code} while requesting {parts.path}') from exc
        except (URLError, TimeoutError) as exc:
            if attempt < 3:
                time.sleep(2 ** (attempt + 1))
            else:
                raise RuntimeError(f'Network error: {exc}') from exc
    raise RuntimeError('API retries exhausted')


def paginate(path):
    """Follow every Deezer 'next' page, keeping track order and duplicates."""
    url = f'{API}/{path.lstrip("/")}'
    url += ('&' if '?' in url else '?') + 'limit=100'
    results = []
    seen = set()
    declared_total = None
    while url:
        if url in seen:
            raise RuntimeError('Pagination loop detected')
        seen.add(url)
        data = get_json(url)
        if not isinstance(data.get('data'), list):
            raise RuntimeError('Unexpected paginated API response')
        if declared_total is None and isinstance(data.get('total'), int):
            declared_total = data['total']
        results.extend(data['data'])
        url = data.get('next')
        if url:
            time.sleep(0.15)
    if declared_total is not None and len(results) != declared_total:
        print(f'WARNING: API returned {len(results)} of {declared_total} items', file=sys.stderr)
    return results, declared_total


def get_id(value, kind):
    if value.isdigit():
        return value
    pattern = r'/(?:profile|user)/(\d+)' if kind == 'user' else r'/playlist/(\d+)'
    match = re.search(pattern + r'(?:[/?#]|$)', value)
    if not match:
        raise ValueError(f'Expected a Deezer {kind} ID or URL; got: {value}')
    return match.group(1)


def safe_name(value):
    name = re.sub(r'[^\w .-]+', '_', value, flags=re.UNICODE).strip(' .')
    return (name[:75].rstrip(' .') or 'untitled')


def make_rows(pl_id, title, tracks):
    for position, track in enumerate(tracks, 1):
        artist = track.get('artist') or {}
        album = track.get('album') or {}
        yield {
            'playlist_id': str(pl_id),
            'playlist_title': title,
            'position': position,
            'track_id': track.get('id', ''),
            'title': track.get('title', ''),
            'artist': artist.get('name', ''),
            'album': album.get('title', ''),
            'duration_seconds': track.get('duration', ''),
            'isrc': track.get('isrc', ''),
            'track_url': track.get('link', ''),
            'artist_url': artist.get('link', ''),
            'album_url': album.get('link', ''),
        }


def write_tsv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, payload):
    with path.open('w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('user', nargs='?', help='Deezer user ID or profile URL (lists public playlists)')
    parser.add_argument('--playlist', action='append', default=[], help='Extra playlist ID/URL; repeatable')
    parser.add_argument('--no-favorites', action='store_true', help='Skip public favourite tracks')
    parser.add_argument('--output', default='deezer_backup', help='Destination directory')
    args = parser.parse_args()
    if not args.user and not args.playlist:
        parser.error('Provide a user ID/profile URL and/or one or more --playlist URLs')

    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    exported_at = datetime.now(timezone.utc).isoformat()
    found = []
    errors = []
    user_id = get_id(args.user, 'user') if args.user else None

    if user_id:
        print(f'Discovering public playlists for Deezer user {user_id} ...')
        try:
            public_playlists, total = paginate(f'user/{user_id}/playlists')
            print(f'Found {len(public_playlists)} listed public playlists')
            found.extend(str(p['id']) for p in public_playlists if p.get('id'))
            if total is not None and len(public_playlists) != total:
                errors.append('Public playlist listing returned fewer entries than expected')
        except (RuntimeError, ValueError) as exc:
            errors.append(f'Cannot list user playlists: {exc}')

    for item in args.playlist:
        found.append(get_id(item, 'playlist'))
    playlist_ids = list(dict.fromkeys(found))  # Only deduplicate playlist IDs, never tracks.
    combined = []
    manifest = {'exported_at_utc': exported_at, 'user_id': user_id,
                'limitations': 'Publicly accessible API data only; private playlists may be missing.',
                'playlists': []}

    def export_playlist(pl_id, title_override=None, tracks_path=None, expected_override=None):
        print(f'Exporting {title_override or pl_id} ...')
        try:
            if tracks_path is None:
                meta = get_json(f'{API}/playlist/{pl_id}')
                title = meta.get('title', f'playlist_{pl_id}')
                expected = meta.get('nb_tracks')
                tracks, reported_total = paginate(f'playlist/{pl_id}/tracks')
            else:
                meta = {'id': pl_id, 'title': title_override, 'type': 'favorites'}
                title = title_override
                expected = expected_override
                tracks, reported_total = paginate(tracks_path)
            rows = list(make_rows(pl_id, title, tracks))
            expected = expected if isinstance(expected, int) else reported_total
            complete = expected is None or len(rows) == expected
            name = f'{safe_name(title)}_{pl_id}'
            write_tsv(destination / f'{name}.tsv', rows)
            write_json(destination / f'{name}.json', {
                'exported_at_utc': exported_at, 'playlist': meta,
                'exported_track_count': len(tracks), 'expected_track_count': expected,
                'complete': complete, 'tracks': tracks,
            })
            combined.extend(rows)
            manifest['playlists'].append({
                'id': pl_id, 'title': title, 'count': len(tracks), 'expected_count': expected,
                'complete': complete, 'json': f'{name}.json', 'tsv': f'{name}.tsv',
            })
            if not complete:
                errors.append(f'{title}: {len(rows)} exported versus {expected} expected')
            print(f'  {len(rows)} tracks -> {name}.json / .tsv' + (' [INCOMPLETE]' if not complete else ''))
        except (RuntimeError, ValueError) as exc:
            msg = f'{title_override or pl_id}: {exc}'
            print(f'WARNING: {msg}', file=sys.stderr)
            errors.append(msg)

    for playlist_id in playlist_ids:
        export_playlist(playlist_id)

    if user_id and not args.no_favorites:
        export_playlist('favorites', title_override='Favorite tracks', tracks_path=f'user/{user_id}/tracks')

    write_tsv(destination / 'ALL_TRACKS.tsv', combined)
    manifest['errors'] = errors
    manifest['complete'] = not errors
    write_json(destination / 'manifest.json', manifest)
    print(f'\nSaved {len(combined)} rows from {len(manifest["playlists"])} collections to {destination.resolve()}')
    if errors:
        print('WARNING: Export may be incomplete. See manifest.json -> errors.', file=sys.stderr)
        return 1
    if not combined:
        print('WARNING: No tracks exported. Check playlist visibility and API access.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
