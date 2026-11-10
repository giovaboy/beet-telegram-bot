"""
parsers.py
Parsers for beet output: normalize beet output into a stable, rich dict
structure used by the bot.

Return schema example:
{
  "status": "has_candidates" | "single_match" | "success" | "no_match" | "error" | "needs_input",
  "path": "/downloads/Album Name",
  "has_multiple_candidates": True|False,
  "selected_index": None | 0..N,
  "candidates": [...],
  "single_match": {...} | None,
  "raw_output": "original beet output",
  "timestamp": "ISO8601 string"
}
"""

import re
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from config import setup_logging, BEET_DEBUG_MODE
from core.match_details import parse_match_details

logger = setup_logging()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ANSI CODES CLEANUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def clean_ansi_codes(s: str) -> str:
    """Remove ANSI escape codes from string."""
    if not s:
        return ''
    ansi_re = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_re.sub('', s)


def normalize_title(title: str) -> str:
    """
    Normalize a title for robust fuzzy matching.
    Handles Unicode characters, punctuation, whitespace normalization.
    """
    if not title:
        return ''

    # Replace various Unicode hyphens/dashes with ASCII hyphen
    title = title.replace('‐', '-').replace('–', '-').replace('—', '-')

    # Replace Unicode quotes
    #title = title.replace(''', "'").replace(''', "'")
    #title = title.replace('"', '"').replace('"', '"')
    title = title.replace('‘', "'").replace('’', "'")
    title = title.replace('“', '"').replace('”', '"')

    # Lowercase
    title = title.lower()

    # Remove common noise words and punctuation for matching
    # Keep letters, numbers, spaces, hyphens
    title = re.sub(r'[^\w\s-]', ' ', title)

    # Normalize whitespace
    title = re.sub(r'\s+', ' ', title).strip()

    return title


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ID EXTRACTION HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_musicbrainz_id(text: str) -> Optional[str]:
    """Extract MusicBrainz release ID (UUID format)."""
    match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', text, re.IGNORECASE)
    return match.group(1) if match else None


# def extract_discogs_id(text: str) -> Optional[str]:
#     """
#     Extract Discogs release ID.
#     Formats supported:
#     - r1234567 (release)
#     - m1234567 (master)
#     - Just the number: 1234567
#     """
#     # Try with r/m prefix first
#     match = re.search(r'\b([rm]\d+)\b', text, re.IGNORECASE)
#     if match:
#         return match.group(1).lower()

#     # Try URL format
#     match = re.search(r'discogs\.com/(?:release|master)/(\d+)', text, re.IGNORECASE)
#     if match:
#         return f"r{match.group(1)}"

#     # Fallback: look for standalone number in parentheses after "Discogs"
#     match = re.search(r'discogs[^\d]*\((\d+)\)', text, re.IGNORECASE)
#     if match:
#         return f"r{match.group(1)}"

#     return None
def extract_discogs_id(text: str) -> Optional[str]:
    """
    Discogs ID as 'r123456' (release) or 'm123456' (master).

    A bare number is accepted only when the text is nothing but the ID
    ("123456", "(123456)", "[r123456]"): this function also scans whole beet
    outputs, where catalogue numbers and years must not be taken for IDs.
    """
    text = text or ''
    if match := re.fullmatch(r'\s*[\[(]?([rm]?)(\d+)[\])]?\s*', text, re.IGNORECASE):
        return f"{(match.group(1) or 'r').lower()}{match.group(2)}"

    # Try URL format first (most reliable)
    match = re.search(r'discogs\.com/(?:release|master)/(\d+)', text, re.IGNORECASE)
    if match:
        return f"r{match.group(1)}"

    # Try explicit Discogs context
    match = re.search(r'discogs[^\d]{0,20}([rm]\d{6,})', text, re.IGNORECASE)
    if match:
        return match.group(1).lower()

    # Last resort: standalone r/m followed by 6+ digits
    match = re.search(r'\b([rm]\d{6,})\b', text, re.IGNORECASE)
    if match:
        return match.group(1).lower()

    return None


def build_url_from_id(source: str, id_value: str) -> Optional[str]:
    """Build full URL from source and ID."""
    if not id_value:
        return None

    if source == 'musicbrainz':
        return f"https://musicbrainz.org/release/{id_value}"

    elif source == 'discogs':
        # Handle both r1234567 and m1234567 formats
        if id_value.startswith('r'):
            return f"https://www.discogs.com/release/{id_value[1:]}"
        elif id_value.startswith('m'):
            return f"https://www.discogs.com/master/{id_value[1:]}"
        else:
            # Assume release if no prefix
            return f"https://www.discogs.com/release/{id_value}"

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SINGLE MATCH PARSER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_beet_match_info(output: str) -> Dict[str, Any]:
    """
    Extract detailed match information from beet output for single match case.
    Returns a dict with fields used by the UI.
    Supports both MusicBrainz and Discogs.
    """
    cleaned = clean_ansi_codes(output or "")
    info = {
        'similarity': None,
        'artist': None,
        'title': None,
        'album': None,
        'year': None,
        'label': None,
        'catalog_num': None,
        'format': None,
        'source': None,
        'mb_url': None,
        'mb_id': None,
        'discogs_url': None,
        'discogs_id': None,
        'differences': []
    }

    # Similarity (e.g. "Match (92.3%)" or "Match 92%")
    if m := re.search(r'Match\s*\(?\s*([\d.]+)%', cleaned, re.IGNORECASE):
        info['similarity'] = float(m.group(1))

    # --- Parse metadata lines early to detect explicit source preference ---
    # MusicBrainz/Discogs metadata line formats are preferred to decide which
    # source the match should be considered from (user-visible). We parse
    # these first so that an incidental UUID elsewhere doesn't override a
    # clear "Discogs, ..." metadata line.
    for line in cleaned.splitlines():
        line = line.strip()
        if re.match(r'^(MusicBrainz|Discogs)', line, re.IGNORECASE):
            parts = [p.strip() for p in line.split(',')]

            # If the metadata line explicitly says 'Discogs' we prefer that
            # as the source for display/selection purposes.
            if parts[0].lower() == 'discogs':
                info['source'] = 'discogs'

            # Index mapping: Source, Format, Year, Country, Label, CatNo
            if len(parts) >= 2 and parts[1].lower() not in ('none', 'n/a', ''):
                info['format'] = parts[1]

            if len(parts) >= 3 and parts[2].isdigit():
                info['year'] = parts[2]

            if len(parts) >= 5 and parts[4].lower() not in ('none', 'n/a', ''):
                info['label'] = parts[4]

            if len(parts) >= 6 and parts[5].lower() not in ('none', 'n/a', ''):
                info['catalog_num'] = parts[5]

            break

    # MusicBrainz ID
    mb_id = extract_musicbrainz_id(cleaned)
    if mb_id:
        info['mb_id'] = mb_id
        info['mb_url'] = build_url_from_id('musicbrainz', mb_id)
        # only set source to musicbrainz if we don't already have an explicit
        # source from metadata (e.g. Discogs)
        if not info['source']:
            info['source'] = 'musicbrainz'

    # Discogs ID
    discogs_id = extract_discogs_id(cleaned)
    if discogs_id:
        info['discogs_id'] = discogs_id
        info['discogs_url'] = build_url_from_id('discogs', discogs_id)
        if not info['source']:
            info['source'] = 'discogs'

    # Extract Artist and Album from main match line
    # Pattern: "Match (X%):\n  Artist - Album"
    if match_line := re.search(
        r'^\s*Match\s*\([\d.]+%?\):\s*\n\s*([^-\n]+?)\s*-\s*([^\n]+)',
        cleaned,
        re.MULTILINE
    ):
        if not info['artist']:
            info['artist'] = match_line.group(1).strip()
        if not info['album']:
            info['album'] = match_line.group(2).strip()

    # Extract metadata from structured line
    # Format: "MusicBrainz, Format, Year, Country, Label, CatNo, ..."
    # or:     "Discogs, Format, Year, Country, Label, CatNo, ..."
    for line in cleaned.splitlines():
        line = line.strip()
        if re.match(r'^(MusicBrainz|Discogs)', line, re.IGNORECASE):
            parts = [p.strip() for p in line.split(',')]

            # Detect source from this line if not already set
            if parts[0].lower() == 'discogs' and not info['source']:
                info['source'] = 'discogs'

            # Index mapping: Source, Format, Year, Country, Label, CatNo
            if len(parts) >= 2 and parts[1].lower() not in ('none', 'n/a', ''):
                info['format'] = parts[1]

            if len(parts) >= 3 and parts[2].isdigit():
                info['year'] = parts[2]

            if len(parts) >= 5 and parts[4].lower() not in ('none', 'n/a', ''):
                info['label'] = parts[4]

            if len(parts) >= 6 and parts[5].lower() not in ('none', 'n/a', ''):
                info['catalog_num'] = parts[5]

            break

    # Extract differences and override artist/album/title if present
    for line in cleaned.splitlines():
        line = line.strip()

        if '* Artist:' in line:
            info['artist'] = line.split('* Artist:', 1)[1].strip()
        elif '* Album:' in line:
            info['album'] = line.split('* Album:', 1)[1].strip()
        elif '* Title:' in line:
            info['title'] = line.split('* Title:', 1)[1].strip()
        elif '≠' in line or '!=' in line:
            info['differences'].append(clean_ansi_codes(line.strip()))

    return info


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MULTIPLE CANDIDATES PARSER (VERBOSE MODE)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_verbose_candidates_1(output: str) -> List[Dict[str, Any]]:
    """
    Two-phase parser for verbose beet output with multiple candidates.
    Supports both MusicBrainz and Discogs sources.

    Phase 1: Extract IDs (MusicBrainz/Discogs) from verbose debug section
    Phase 2: Extract user-friendly details from candidates list
    Phase 3: Match IDs to details using normalized title matching

    Returns a list of candidate dicts with complete information.
    """
    cleaned = clean_ansi_codes(output or "")
    candidates = []

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 1: Extract IDs from verbose debug section
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Pattern: "Candidate: Artist - Album (id)\n...Distance: 0.XX"

    id_map = {}  # Map: normalized_title -> (source, id_value, distance)

    # MusicBrainz candidates
    mb_pattern = re.compile(
        r'Candidate:\s*(.+?)\s*\(([a-f0-9-]{36})\)\s*\n'
        r'.*?Distance:\s*([\d.]+)',
        re.DOTALL | re.IGNORECASE
    )

    for match in mb_pattern.finditer(cleaned):
        full_title = match.group(1).strip()
        mb_id = match.group(2)
        distance = float(match.group(3))

        normalized = normalize_title(full_title)
        id_map[normalized] = ('musicbrainz', mb_id, distance)

        logger.debug(f"Found MusicBrainz ID: {mb_id} for '{full_title}' (distance: {distance})")

    # ✅ FIX: Discogs candidates - ENHANCED pattern for all formats
    # Pattern 1: With r/m prefix: (r1234567) or (m1234567)
    discogs_pattern_prefixed = re.compile(
        r'Candidate:\s*(.+?)\s*\(([rm]\d+)\)\s*\n'
        r'.*?Distance:\s*([\d.]+)',
        re.DOTALL | re.IGNORECASE
    )

    for match in discogs_pattern_prefixed.finditer(cleaned):
        full_title = match.group(1).strip()
        discogs_id = match.group(2).lower()  # Already has r/m prefix
        distance = float(match.group(3))

        normalized = normalize_title(full_title)
        if normalized not in id_map:  # MusicBrainz has priority
            id_map[normalized] = ('discogs', discogs_id, distance)
            logger.debug(f"Found Discogs ID (prefixed): {discogs_id} for '{full_title}'")

    # ✅ FIX: Pattern 2: Pure number format: (1234567) - MOST COMMON
    # This catches the format you're seeing: "... (2965563)\n...Distance: 0.56"
    discogs_pattern_pure = re.compile(
        r'discogs:.*?Candidate:\s*(.+?)\s*\((\d{6,})\)\s*\n'  # Note 'discogs:' prefix in verbose mode
        r'.*?Distance:\s*([\d.]+)',
        re.DOTALL | re.IGNORECASE
    )

    for match in discogs_pattern_pure.finditer(cleaned):
        full_title = match.group(1).strip()
        discogs_number = match.group(2)
        distance = float(match.group(3))

        normalized = normalize_title(full_title)
        if normalized not in id_map:
            # Add 'r' prefix (assume release not master)
            discogs_id = f"r{discogs_number}"
            id_map[normalized] = ('discogs', discogs_id, distance)
            logger.debug(f"Found Discogs ID (pure number): {discogs_id} for '{full_title}'")

    # ✅ FIX: Pattern 3: Fallback - any "Getting master release" line
    discogs_master_pattern = re.compile(
        r'discogs:\s*Getting\s+(?:master|release)\s+release\s+(\d+)',
        re.IGNORECASE
    )

    # Build a map of Discogs API calls to track which ID belongs to which candidate
    discogs_api_calls = []
    for match in discogs_master_pattern.finditer(cleaned):
        discogs_number = match.group(1)
        discogs_api_calls.append(discogs_number)
        logger.debug(f"Found Discogs API call for ID: {discogs_number}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 2: Extract details from user-friendly candidates section
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Pattern matches:
    # 1. (35.7%) Artist - Album
    #            ≠ differences...
    #            MusicBrainz, Format, Year, Country, Label, CatNo, ...

    block_pattern = re.compile(
        r'^\s*(\d+)\.\s*\(([\d.]+)%\)\s*(.+?)\s*\n'  # Number, similarity, artist-album
        r'\s*≠\s*(.+?)\s*\n'                         # Differences line
        r'\s*(MusicBrainz|Discogs),\s*(.+?)(?=\n\s*\d+\.|$)',  # Metadata line
        re.MULTILINE | re.IGNORECASE | re.DOTALL
    )

    for match in block_pattern.finditer(cleaned):
        idx = int(match.group(1))
        similarity = float(match.group(2))
        artist_album = match.group(3).strip()
        differences_line = match.group(4).strip()
        source = match.group(5).lower()
        metadata = match.group(6).strip()

        # Parse metadata line: Format, Year, Country, Label, CatNo, ...
        parts = [p.strip() for p in metadata.split(',')]

        format_ = parts[0] if len(parts) > 0 and parts[0].lower() != 'none' else None
        year = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        country = parts[2] if len(parts) > 2 and parts[2].lower() not in ('none', 'n/a', '') else None
        label = parts[3] if len(parts) > 3 and parts[3].lower() not in ('none', 'n/a', '') else None
        catno = parts[4] if len(parts) > 4 and parts[4].lower() not in ('none', 'n/a', '') else None

        # Split "Artist - Album"
        artist = artist_album
        album = None
        if ' - ' in artist_album:
            artist, album = artist_album.split(' - ', 1)

        # Parse differences
        differences = [d.strip() for d in differences_line.split(',') if d.strip()]

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # PHASE 3: Match with ID map using normalized title
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        normalized = normalize_title(artist_album)

        id_source = source  # Default to source from metadata line
        id_value = None
        distance = None
        mb_id = None
        mb_url = None
        discogs_id = None
        discogs_url = None

        if normalized in id_map:
            id_source, id_value, distance = id_map[normalized]
            logger.debug(f"Matched candidate #{idx} to {id_source} ID {id_value}")
        else:
            # Fuzzy fallback: try partial matching
            for key, (candidate_source, candidate_id, candidate_dist) in id_map.items():
                if normalized in key or key in normalized:
                    id_source = candidate_source
                    id_value = candidate_id
                    distance = candidate_dist
                    logger.debug(f"Fuzzy matched candidate #{idx} to {id_source} ID {id_value}")
                    break

        # Build URLs based on source
        if id_value:
            if id_source == 'musicbrainz':
                mb_id = id_value
                mb_url = build_url_from_id('musicbrainz', id_value)
            elif id_source == 'discogs':
                discogs_id = id_value
                discogs_url = build_url_from_id('discogs', id_value)

        candidates.append({
            'number': idx,
            'similarity': similarity,
            'artist': artist.strip(),
            'album': album.strip() if album else None,
            'source': id_source,
            'format': format_,
            'year': year,
            'country': country,
            'label': label,
            'catalog_num': catno,
            'mb_id': mb_id,
            'mb_url': mb_url,
            'discogs_id': discogs_id,
            'discogs_url': discogs_url,
            'distance': distance,
            'differences': differences,
            'info_short': f"{artist.strip()} - {album.strip() if album else 'Unknown'}",
            'raw_output': match.group(0)
        })

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FALLBACK: If no structured candidates found, try ID-only extraction
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if not candidates and id_map:
        logger.warning("No structured candidates found, using ID-only fallback")
        for idx, (normalized, (source, id_value, distance)) in enumerate(id_map.items(), 1):
            mb_id = None
            mb_url = None
            discogs_id = None
            discogs_url = None

            if source == 'musicbrainz':
                mb_id = id_value
                mb_url = build_url_from_id('musicbrainz', id_value)
            elif source == 'discogs':
                discogs_id = id_value
                discogs_url = build_url_from_id('discogs', id_value)

            candidates.append({
                'number': idx,
                'similarity': round((1 - distance) * 100, 1),  # Convert distance to similarity
                'artist': None,
                'album': None,
                'source': source,
                'format': None,
                'year': None,
                'country': None,
                'label': None,
                'catalog_num': None,
                'mb_id': mb_id,
                'mb_url': mb_url,
                'discogs_id': discogs_id,
                'discogs_url': discogs_url,
                'distance': distance,
                'differences': [],
                'info_short': normalized,
                'raw_output': cleaned
            })

    return candidates


_MB_UUID_RE = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', re.IGNORECASE)
# beet -vv: "Candidate: Artist - Album (id) from MusicBrainz" (older: without "from ...")
_CANDIDATE_ID_RE = re.compile(
    r'^Candidate:\s*(?P<title>.+?)\s*\((?P<id>[^()\s]+)\)(?:\s+from\s+(?P<source>\w+))?\s*$',
    re.MULTILINE)
_DISTANCE_RE = re.compile(r'Distance:\s*([\d.]+)')
# Candidate list: "  1. 46.5% Artist - Album" (older beet: "1. (46.5%) Artist - Album")
_CANDIDATE_LINE_RE = re.compile(r'^\s*(\d+)\.\s*\(?([\d.]+)%\)?\s+(.+?)\s*$')
_PENALTY_LINE_RE = re.compile(r'^\s*≠\s*(.+?)\s*$')
_DISAMBIG_LINE_RE = re.compile(r'^\s*(MusicBrainz|Discogs)\s*,\s*(.*?)\s*$', re.IGNORECASE)


def _value(text: Optional[str]) -> Optional[str]:
    text = (text or '').strip()
    return None if text.lower() in ('', 'none', 'n/a', '0') else text


def _candidate_ids(cleaned: str) -> List[Dict[str, Any]]:
    """Release IDs from the verbose "Candidate: ... (id) from <source>" lines."""
    found = []
    for match in _CANDIDATE_ID_RE.finditer(cleaned):
        raw_id = match.group('id')
        source = (match.group('source') or '').lower()
        if source not in ('musicbrainz', 'discogs'):
            if _MB_UUID_RE.fullmatch(raw_id):
                source = 'musicbrainz'
            elif re.fullmatch(r'[rm]?\d+', raw_id, re.IGNORECASE):
                source = 'discogs'
            else:
                continue
        if source == 'discogs':
            raw_id = raw_id.lower() if raw_id[0].lower() in 'rm' else f"r{raw_id}"
        # Distance printed after this candidate, before the next one
        rest = cleaned[match.end():]
        next_candidate = rest.find('\nCandidate:')
        distance = _DISTANCE_RE.search(rest if next_candidate == -1 else rest[:next_candidate])
        found.append({
            'title': match.group('title').strip(),
            'source': source,
            'id': raw_id,
            'distance': float(distance.group(1)) if distance else None,
            'used': False,
        })
    return found


def _candidate_list(cleaned: str) -> List[Dict[str, Any]]:
    """The numbered list beet shows to the user, with penalties and release info."""
    lines = cleaned.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == 'Candidates:'), None)
    if start is None:
        return []
    listed: List[Dict[str, Any]] = []
    for line in lines[start + 1:]:
        if match := _CANDIDATE_LINE_RE.match(line):
            listed.append({'number': int(match.group(1)), 'similarity': float(match.group(2)),
                           'title': match.group(3), 'penalties': [], 'source': None, 'info': []})
        elif listed and (match := _PENALTY_LINE_RE.match(line)) and not listed[-1]['penalties']:
            listed[-1]['penalties'] = [p.strip() for p in match.group(1).split(',')
                                       if p.strip() and p.strip() != '...']
        elif listed and (match := _DISAMBIG_LINE_RE.match(line)):
            listed[-1]['source'] = match.group(1).lower()
            listed[-1]['info'] = [p.strip() for p in match.group(2).split(',')]
        elif listed:
            break  # beet's prompt
    return listed


def _match_id(entry: Dict[str, Any], ids: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    The verbose line of this candidate: same title and source. Several releases
    can share a title (e.g. two pressings): pick the closest distance; each ID
    is used once.
    """
    title = normalize_title(entry['title'])
    expected = 1 - entry['similarity'] / 100
    pool = [i for i in ids if not i['used'] and (entry['source'] in (None, i['source']))]
    same = [i for i in pool if normalize_title(i['title']) == title]
    if not same:
        same = [i for i in pool if title and (title in normalize_title(i['title'])
                                              or normalize_title(i['title']) in title)]
    if not same:
        return None
    best = min(same, key=lambda i: abs((i['distance'] if i['distance'] is not None else expected) - expected))
    best['used'] = True
    return best


def parse_verbose_candidates(output: str) -> List[Dict[str, Any]]:
    """
    Candidates of a beet import that needs a choice: the numbered list beet
    prints (similarity, artist - album, penalties, media/year/country/label/
    catalogue number), each with its release ID taken from the verbose
    "Candidate: ... (id) from <source>" lines.
    """
    cleaned = clean_ansi_codes(output or "")
    ids = _candidate_ids(cleaned)
    candidates = []

    for entry in _candidate_list(cleaned):
        matched = _match_id(entry, ids)
        source = (matched or {}).get('source') or entry['source']
        release_id = (matched or {}).get('id')
        artist, _, album = entry['title'].partition(' - ')
        info = entry['info'] + [None] * 6
        media, year, country, label, catno = (_value(v) for v in info[:5])

        mb_id = release_id if source == 'musicbrainz' else None
        discogs_id = release_id if source == 'discogs' else None
        if not release_id:
            logger.warning(f"No release ID found for candidate #{entry['number']}: {entry['title']}")

        candidates.append({
            'number': entry['number'],
            'similarity': entry['similarity'],
            'artist': artist.strip() or None,
            'album': album.strip() or None,
            'source': source,
            'format': media,
            'year': year,
            'country': country,
            'label': label,
            'catalog_num': catno,
            'mb_id': mb_id,
            'mb_url': build_url_from_id('musicbrainz', mb_id) if mb_id else None,
            'discogs_id': discogs_id,
            'discogs_url': build_url_from_id('discogs', discogs_id) if discogs_id else None,
            'distance': (matched or {}).get('distance'),
            'differences': entry['penalties'],
            'info_short': entry['title'],
        })

    # No readable list (unexpected format): offer what the verbose lines say
    if not candidates:
        for number, item in enumerate(ids, 1):
            artist, _, album = item['title'].partition(' - ')
            similarity = round((1 - item['distance']) * 100, 1) if item['distance'] is not None else None
            mb_id = item['id'] if item['source'] == 'musicbrainz' else None
            discogs_id = item['id'] if item['source'] == 'discogs' else None
            candidates.append({
                'number': number, 'similarity': similarity,
                'artist': artist.strip() or None, 'album': album.strip() or None,
                'source': item['source'], 'format': None, 'year': None, 'country': None,
                'label': None, 'catalog_num': None,
                'mb_id': mb_id, 'mb_url': build_url_from_id('musicbrainz', mb_id) if mb_id else None,
                'discogs_id': discogs_id,
                'discogs_url': build_url_from_id('discogs', discogs_id) if discogs_id else None,
                'distance': item['distance'], 'differences': [], 'info_short': item['title'],
            })
        candidates.sort(key=lambda c: -(c['similarity'] or 0))
        for number, candidate in enumerate(candidates, 1):
            candidate['number'] = number
    return candidates


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HIGH-LEVEL PARSE ENTRYPOINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_beet_output(stdout: str, stderr: str, path: str) -> Dict[str, Any]:
    """
    High-level parser: classify beet output and build the canonical import dict
    used by the bot. Always returns a dict with the schema defined above.
    Supports both MusicBrainz and Discogs sources.
    """
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STEP 1: Clean the output (remove chroma and debug noise)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    raw = (stdout or "") + "\n" + (stderr or "")
    cleaned_raw = clean_beet_output(raw)  # 🎯 NEW: Clean before parsing

    cleaned_low = cleaned_raw.lower()

    now = datetime.datetime.utcnow().isoformat() + "Z"

    base = {
        'status': 'needs_input',
        'path': str(path),
        'has_multiple_candidates': False,
        'selected_index': None,
        'candidates': [],
        'single_match': None,
        'raw_output': cleaned_raw,  # 🎯 Store cleaned output, not raw
        'timestamp': now
    }

    # Direct success
    if any(k in cleaned_low for k in ['successfully imported', 'already in library', 'imported and tagged']):
        base['status'] = 'success'
        return base

    # Single match (heuristic: presence of "match (" plus musicbrainz/discogs url/id)
    has_match = 'match (' in cleaned_low
    has_mb = 'musicbrainz.org' in cleaned_low or extract_musicbrainz_id(cleaned_raw)
    has_discogs = 'discogs' in cleaned_low and extract_discogs_id(cleaned_raw)
    has_candidates_section = 'candidates:' not in cleaned_low

    if has_match and (has_mb or has_discogs) and has_candidates_section:
        base['status'] = 'single_match'
        info = parse_beet_match_info(cleaned_raw)  # 🎯 Use cleaned output

        # Put the info into the same schema as a candidate for uniformity
        candidate = {
            'number': 1,
            'mb_id': info.get('mb_id'),
            'mb_url': info.get('mb_url'),
            'discogs_id': info.get('discogs_id'),
            'discogs_url': info.get('discogs_url'),
            'artist': info.get('artist'),
            'album': info.get('album'),
            'year': info.get('year'),
            'country': None,
            'label': info.get('label'),
            'catalog_num': info.get('catalog_num'),
            'format': info.get('format'),
            'source': info.get('source'),
            'similarity': info.get('similarity'),
            'differences': info.get('differences', []),
            # Structured view of the same block (album/track changes, missing tracks...)
            'details': parse_match_details(cleaned_raw),
            'info_short': f"{info.get('artist') or 'Unknown'} - {info.get('album') or 'Unknown'}",
            'raw_output': cleaned_raw  # 🎯 Cleaned output
        }
        base['single_match'] = candidate
        return base

    # Multiple candidates (use verbose parser)
    if 'candidates:' in cleaned_low:
        logger.debug('Parsing multiple candidates...')
        candidates = parse_verbose_candidates(cleaned_raw)  # 🎯 Use cleaned output

        if candidates:
            base['status'] = 'has_candidates'
            base['has_multiple_candidates'] = True
            base['candidates'] = candidates
            base['selected_index'] = None
            logger.debug(f"Found {len(candidates)} candidates")
            return base

    # No matches
    if 'no matching release found' in cleaned_low or 'no candidates' in cleaned_low:
        base['status'] = 'no_match'
        return base

    # Low similarity
    if 'low similarity' in cleaned_low:
        base['status'] = 'low_similarity'
        return base

    # Fallback: keep raw output for debugging and surface 'needs_input'
    base['status'] = 'needs_input'
    return base



def clean_chroma_noise(output: str) -> str:
    """
    Remove all chromaprint/chroma related lines from beet output.
    These lines add noise without useful information for the user.

    Removes:
    - "chroma: chroma: fingerprinted ..."
    - "chroma: matched recordings [...] on releases [...]"
    - "chroma: no match found"
    - Any line starting with "chroma:"

    Args:
        output: Raw beet output with chroma noise

    Returns:
        Cleaned output without chroma lines
    """
    if not output:
        return ''

    lines = output.splitlines()
    cleaned_lines = []

    skip_next = False

    for line in lines:
        # Skip lines starting with "chroma:"
        if line.strip().startswith('chroma:'):
            # Check if this is a multi-line chroma output
            # (e.g., "matched recordings [...] on releases [...]" can span multiple lines)
            if 'matched recordings' in line or 'on releases' in line:
                skip_next = True  # Skip potential continuation
            continue

        # Skip continuation lines after matched recordings
        if skip_next:
            # Check if this line is part of the long list
            if line.strip().startswith(('[', "'")):
                continue
            else:
                skip_next = False

        cleaned_lines.append(line)

    return '\n'.join(cleaned_lines)


def clean_verbose_debug_lines(output: str) -> str:
    """
    Remove verbose debug lines that are not useful for the user.

    Removes:
    - "Sending event: ..."
    - "user configuration: ..."
    - "data directory: ..."
    - "plugin paths: ..."
    - "Loading plugins: ..."
    - "fetchart: ..." (disabled art sources)
    - "library database: ..."
    - "library directory: ..."

    Args:
        output: Raw beet output

    Returns:
        Cleaned output without verbose debug lines
    """
    if not output:
        return ''

    lines = output.splitlines()
    cleaned_lines = []

    # Patterns to filter out
    skip_patterns = [
        'Sending event:',
        'user configuration:',
        'data directory:',
        'plugin paths:',
        'Loading plugins:',
        'fetchart:',
        'library database:',
        'library directory:',
        'Disabling art source',
    ]

    for line in lines:
        stripped = line.strip()

        # Skip if line matches any skip pattern
        if any(stripped.startswith(pattern) for pattern in skip_patterns):
            continue

        cleaned_lines.append(line)

    return '\n'.join(cleaned_lines)


def clean_beet_output(output: str) -> str:
    """
    Master cleaning function: removes all noise from beet output.

    Applies:
    1. ANSI code removal
    2. Chroma noise removal
    3. Verbose debug lines removal

    Args:
        output: Raw beet output

    Returns:
        Clean, user-friendly output
    """
    # Step 1: Remove ANSI codes
    cleaned = clean_ansi_codes(output)

    # Step 2: Remove chroma noise
    cleaned = clean_chroma_noise(cleaned)

    # Step 3: Remove verbose debug lines
    if not BEET_DEBUG_MODE:
        cleaned = clean_verbose_debug_lines(cleaned)

    # Step 4: Normalize whitespace (remove excessive blank lines)
    lines = cleaned.splitlines()
    cleaned_lines = []
    prev_blank = False

    for line in lines:
        is_blank = not line.strip()

        # Skip consecutive blank lines
        if is_blank and prev_blank:
            continue

        cleaned_lines.append(line)
        prev_blank = is_blank

    return '\n'.join(cleaned_lines).strip()