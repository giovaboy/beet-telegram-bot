"""
Structured parsing of the match block beet prints during an import:

      Match (76.3%):
      Christian Ferras - The SWR Recordings
      ≠ artist, album, tracks, data source
      MusicBrainz, 2xCD, 2022, None, SWR Classic, None, None
      https://musicbrainz.org/release/...
      ≠ Artist: <current> -> <new>            (or "* Artist: <same>")
      ≠ Album: <current> -> <new>
      * CD 1                                  (medium header, multi-disc only)
         ≠ (#1) <current title> (11:36) -> (#1) <new title> (11:36)
    Missing tracks (2/8 - 25.0%):
     ! <title> (#7) (6:41)
    Unmatched tracks (1):
     ! <title> (#7) (5:12)

beet only prints one line per change when the line fits the terminal width,
which is why BeetImportManager runs it with COLUMNS large and NO_COLOR set.
With older beet versions (< 2.6) COLUMNS is ignored and long lines are wrapped
on two columns: those continuation lines cannot be stitched back reliably, so
the result is flagged with 'wrapped': True and titles may be incomplete.
"""
import re
import unicodedata
from typing import Any, Dict, List, Optional

# beet's default ui.length_diff_thresh (seconds)
LENGTH_DIFF_THRESHOLD = 10

_LENGTH = r'\d+:\d{2}(?::\d{2})?'
_MATCH_RE = re.compile(r'^\s*Match \((\d+(?:\.\d+)?)%\):\s*$')
_ALBUM_CHANGE_RE = re.compile(r'^\s*≠\s+(Artist|Album|Title):\s?(.*?)\s+->\s+(.*?)\s*$')
_ALBUM_SAME_RE = re.compile(r'^\s*\*\s+(Artist|Album|Title):\s*(.*?)\s*$')
_TRACK_CHANGE_RE = re.compile(
    rf'^\s*≠\s+\(#([\w-]+)\)\s+(.*?)\s*\(({_LENGTH})\)'
    rf'\s+->\s+\(#([\w-]+)\)\s+(.*?)\s*\(({_LENGTH})\)\s*$')
_TRACK_SAME_RE = re.compile(rf'^\s*\*\s+\(#([\w-]+)\)\s+(.*?)\s*\(({_LENGTH})\)\s*$')
_MEDIUM_RE = re.compile(r'^\s*\*\s+(.+?)\s*$')
_PENALTIES_RE = re.compile(r'^\s*≠\s+([a-z_ ]+(?:,\s*[a-z_ ]+)*)\s*$')
_URL_RE = re.compile(r'^\s*(https?://\S+)\s*$')
_MISSING_RE = re.compile(r'^\s*Missing tracks \((\d+)/(\d+) - ([\d.]+)%\):\s*$')
_UNMATCHED_RE = re.compile(r'^\s*Unmatched tracks \((\d+)\):\s*$')
_EXTRA_TRACK_RE = re.compile(rf'^\s*!\s+(.*?)\s+\(#([\w-]+)\)(?:\s+\(({_LENGTH})\))?\s*$')


def length_seconds(value: Optional[str]) -> Optional[int]:
    """'11:36' → 696, '1:02:03' → 3723."""
    if not value:
        return None
    seconds = 0
    for part in value.split(':'):
        seconds = seconds * 60 + int(part)
    return seconds


_PUNCT_MAP = str.maketrans({
    '“': '"', '”': '"', '„': '"', '‟': '"', '″': '"', '«': '"', '»': '"',
    '‘': "'", '’': "'", '‚': "'", '‛': "'", '′': "'", '`': "'",
    '–': '-', '—': '-', '‐': '-', '‑': '-', '‒': '-', '−': '-',
    '…': '...',
})


def normalize_title(title: str) -> str:
    """Comparable form: case, quotes, dashes, accents and spacing don't matter."""
    text = unicodedata.normalize('NFKD', title or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.translate(_PUNCT_MAP).casefold()
    text = re.sub(r'\s+([,.;:!?)\]])', r'\1', text)
    text = re.sub(r'([(\[])\s+', r'\1', text)
    # Opening and closing quotes look the same once mapped: ignore spacing
    # around them ('“Kreutzer ”' and '"Kreutzer"' are the same title)
    text = re.sub(r'\s*"\s*', '"', text)
    return re.sub(r'\s+', ' ', text).strip()


def classify_track(track: Dict[str, Any]) -> List[str]:
    """
    Kinds of change of one track line (a line can have several):
    'title' (real wording change), 'minor' (case/punctuation/quotes only),
    'length' (≥ LENGTH_DIFF_THRESHOLD seconds), 'index' (track number).
    """
    kinds = []
    if track['old_title'] != track['new_title']:
        same = normalize_title(track['old_title']) == normalize_title(track['new_title'])
        kinds.append('minor' if same else 'title')
    old_len, new_len = length_seconds(track['old_length']), length_seconds(track['new_length'])
    if old_len is not None and new_len is not None and abs(old_len - new_len) >= LENGTH_DIFF_THRESHOLD:
        kinds.append('length')
    if track['old_index'] != track['new_index']:
        kinds.append('index')
    return kinds


def _parse_disambig(line: str) -> List[str]:
    return [part.strip() for part in line.split(',') if part.strip() and part.strip() != 'None']


def parse_match_details(output: str) -> Dict[str, Any]:
    """Parse the first match block of a beet import output (ANSI codes already removed)."""
    details: Dict[str, Any] = {
        'found': False,
        'similarity': None,
        'artist': None,
        'album': None,
        'penalties': [],
        'disambig': [],
        'url': None,
        'album_changes': [],     # [{'field', 'old', 'new'}]
        'album_same': {},        # {'Artist': value, ...}
        'tracks': [],            # changed tracks, see _TRACK_CHANGE_RE
        'unchanged_tracks': 0,
        'missing': None,         # {'count', 'total', 'percent', 'items': [...]}
        'unmatched': None,       # {'count', 'items': [...]}
        'wrapped': False,
    }
    lines = (output or '').splitlines()
    start = next((i for i, line in enumerate(lines) if _MATCH_RE.match(line)), None)
    if start is None:
        return details

    details['found'] = True
    details['similarity'] = float(_MATCH_RE.match(lines[start]).group(1))

    header_done = False
    medium = None
    section = None  # 'missing' | 'unmatched' while reading "! ..." lines

    for index, line in enumerate(lines[start + 1:], start + 1):
        if not line.strip():
            continue
        if _MATCH_RE.match(line):
            break  # next match block (not expected in timid mode)

        # ── Missing / unmatched sections ──────────────────────────────
        if m := _MISSING_RE.match(line):
            details['missing'] = {'count': int(m.group(1)), 'total': int(m.group(2)),
                                  'percent': float(m.group(3)), 'items': []}
            section = 'missing'
            continue
        if m := _UNMATCHED_RE.match(line):
            details['unmatched'] = {'count': int(m.group(1)), 'items': []}
            section = 'unmatched'
            continue
        if section and (m := _EXTRA_TRACK_RE.match(line)):
            details[section]['items'].append(
                {'title': m.group(1), 'index': m.group(2), 'length': m.group(3)})
            continue
        if section:
            break  # first line after the lists: beet's prompt

        # ── Header: "Artist - Album", penalties, disambiguation, URL ──
        if not header_done:
            if details['artist'] is None and details['album'] is None and ' - ' in line \
                    and not line.strip().startswith(('≠', '*')):
                artist, _, album = line.strip().partition(' - ')
                details['artist'], details['album'] = artist, album
                continue
            if (m := _PENALTIES_RE.match(line)) and not details['penalties']:
                details['penalties'] = [p.strip() for p in m.group(1).split(',')]
                continue
            if m := _URL_RE.match(line):
                details['url'] = m.group(1)
                continue
            if not line.strip().startswith(('≠', '*')) and ',' in line and not details['disambig']:
                details['disambig'] = _parse_disambig(line)
                continue

        # ── Album-level fields ────────────────────────────────────────
        if m := _ALBUM_CHANGE_RE.match(line):
            header_done = True
            details['album_changes'].append({'field': m.group(1), 'old': m.group(2), 'new': m.group(3)})
            continue
        if m := _ALBUM_SAME_RE.match(line):
            header_done = True
            details['album_same'][m.group(1)] = m.group(2)
            continue

        # ── Tracks ────────────────────────────────────────────────────
        if m := _TRACK_CHANGE_RE.match(line):
            header_done = True
            track = {
                'medium': medium,
                'old_index': m.group(1), 'old_title': m.group(2), 'old_length': m.group(3),
                'new_index': m.group(4), 'new_title': m.group(5), 'new_length': m.group(6),
            }
            track['kinds'] = classify_track(track)
            details['tracks'].append(track)
            continue
        if _TRACK_SAME_RE.match(line):
            header_done = True
            details['unchanged_tracks'] += 1
            continue
        if header_done and (m := _MEDIUM_RE.match(line)):
            medium = m.group(1)
            continue

        # Anything else inside the block: indented continuation of a
        # wrapped line (old beet ignoring COLUMNS), or beet's prompt.
        if header_done and line.startswith('   '):
            details['wrapped'] = True
            continue
        if header_done:
            break

    return details
