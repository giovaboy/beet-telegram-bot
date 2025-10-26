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
from typing import List, Dict, Any, Optional, Tuple
from difflib import SequenceMatcher
from config import setup_logging

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
    title = title.replace(''', "'").replace(''', "'")
    title = title.replace('"', '"').replace('"', '"')
    
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


def extract_discogs_id(text: str) -> Optional[str]:
    """
    Extract Discogs release ID.
    Formats supported:
    - r1234567 (release)
    - m1234567 (master)
    - Just the number: 1234567
    """
    # Try with r/m prefix first
    match = re.search(r'\b([rm]\d+)\b', text, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    
    # Try URL format
    match = re.search(r'discogs\.com/(?:release|master)/(\d+)', text, re.IGNORECASE)
    if match:
        return f"r{match.group(1)}"
    
    # Fallback: look for standalone number in parentheses after "Discogs"
    match = re.search(r'discogs[^\d]*\((\d+)\)', text, re.IGNORECASE)
    if match:
        return f"r{match.group(1)}"
    
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
# CHARACTER-LEVEL DIFF FORMATTING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def char_diff(old: str, new: str) -> Tuple[str, str]:
    """
    Create character-level diff highlighting exact changes.
    
    Returns:
        (formatted_old, formatted_new) with MarkdownV2 emphasis on differences
    """
    from telegram.helpers import escape_markdown
    
    if not old or not new:
        return (
            f"~~{escape_markdown(old or '', version=2)}~~" if old else "",
            f"__*{escape_markdown(new or '', version=2)}*__" if new else ""
        )
    
    matcher = SequenceMatcher(None, old, new)
    
    old_parts = []
    new_parts = []
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_chunk = old[i1:i2]
        new_chunk = new[j1:j2]
        
        if tag == 'equal':
            # Same text, no formatting
            old_parts.append(escape_markdown(old_chunk, version=2))
            new_parts.append(escape_markdown(new_chunk, version=2))
        
        elif tag == 'replace':
            # Changed text: strikethrough old, underline+bold new
            old_parts.append(f"~~{escape_markdown(old_chunk, version=2)}~~")
            new_parts.append(f"__*{escape_markdown(new_chunk, version=2)}*__")
        
        elif tag == 'delete':
            # Deleted text: strikethrough
            old_parts.append(f"~~{escape_markdown(old_chunk, version=2)}~~")
        
        elif tag == 'insert':
            # Inserted text: underline+bold
            new_parts.append(f"__*{escape_markdown(new_chunk, version=2)}*__")
    
    return ''.join(old_parts), ''.join(new_parts)


def word_diff(old: str, new: str) -> Tuple[str, str]:
    """
    Create word-level diff (faster, cleaner for long strings).
    
    Similar to char_diff but operates on words instead of characters.
    Better for long strings like album titles with many words.
    """
    from telegram.helpers import escape_markdown
    
    if not old or not new:
        return (
            f"~~{escape_markdown(old or '', version=2)}~~" if old else "",
            f"__*{escape_markdown(new or '', version=2)}*__" if new else ""
        )
    
    old_words = old.split()
    new_words = new.split()
    
    matcher = SequenceMatcher(None, old_words, new_words)
    
    old_parts = []
    new_parts = []
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_chunk = old_words[i1:i2]
        new_chunk = new_words[j1:j2]
        
        if tag == 'equal':
            old_parts.extend([escape_markdown(w, version=2) for w in old_chunk])
            new_parts.extend([escape_markdown(w, version=2) for w in new_chunk])
        
        elif tag == 'replace':
            old_parts.extend([f"~~{escape_markdown(w, version=2)}~~" for w in old_chunk])
            new_parts.extend([f"__*{escape_markdown(w, version=2)}*__" for w in new_chunk])
        
        elif tag == 'delete':
            old_parts.extend([f"~~{escape_markdown(w, version=2)}~~" for w in old_chunk])
        
        elif tag == 'insert':
            new_parts.extend([f"__*{escape_markdown(w, version=2)}*__" for w in new_chunk])
    
    return ' '.join(old_parts), ' '.join(new_parts)


def smart_diff(old: str, new: str, char_threshold: int = 50) -> Tuple[str, str]:
    """
    Intelligently choose between character-level and word-level diff.
    
    - Use char_diff for short strings (< char_threshold chars)
    - Use word_diff for long strings (better readability)
    
    Args:
        old: Original value
        new: New value
        char_threshold: Max length for character-level diff
    
    Returns:
        (formatted_old, formatted_new)
    """
    if not old and not new:
        return "", ""
    
    max_len = max(len(old or ''), len(new or ''))
    
    if max_len <= char_threshold:
        return char_diff(old, new)
    else:
        return word_diff(old, new)


def parse_and_format_difference(diff_line: str) -> Dict[str, Any]:
    """
    Parse a difference line from beet output and return structured info.
    
    Examples:
        "≠ artist (Foo Bar -> Baz Qux)"
        "≠ tracks (12 vs 15)"
        "* Artist: Foo Bar"
        "missing tracks"
    
    Returns:
        {
            'type': 'field_change' | 'mismatch' | 'missing' | 'extra' | 'generic',
            'field': 'artist' | 'album' | 'tracks' | ...,
            'old_value': '...',
            'new_value': '...',
            'raw': 'original line'
        }
    """
    cleaned = clean_ansi_codes(diff_line).strip()
    
    result = {
        'type': 'generic',
        'field': None,
        'old_value': None,
        'new_value': None,
        'raw': cleaned
    }
    
    # Pattern 1: "≠ field (old -> new)" or "≠ field (old vs new)"
    pattern1 = re.compile(r'[≠!=]\s*(.+?)\s*\((.+?)\s*(?:->|→|vs)\s*(.+?)\)', re.IGNORECASE)
    if m := pattern1.search(cleaned):
        result['type'] = 'field_change'
        result['field'] = m.group(1).strip()
        result['old_value'] = m.group(2).strip()
        result['new_value'] = m.group(3).strip()
        return result
    
    # Pattern 2: "* Field: value"
    pattern2 = re.compile(r'\*\s*(.+?):\s*(.+)', re.IGNORECASE)
    if m := pattern2.search(cleaned):
        result['type'] = 'field_change'
        result['field'] = m.group(1).strip()
        result['new_value'] = m.group(2).strip()
        return result
    
    # Pattern 3: "missing tracks" / "extra tracks" / "unmatched tracks"
    if 'missing' in cleaned.lower():
        result['type'] = 'missing'
        result['field'] = cleaned.replace('missing', '').strip()
        return result
    
    if 'extra' in cleaned.lower() or 'unmatched' in cleaned.lower():
        result['type'] = 'extra'
        result['field'] = cleaned.replace('extra', '').replace('unmatched', '').strip()
        return result
    
    # Pattern 4: Generic mismatch (just "≠ something")
    if cleaned.startswith(('≠', '!=')):
        result['type'] = 'mismatch'
        result['field'] = cleaned.lstrip('≠!=').strip()
        return result
    
    return result


def format_difference_with_diff(diff: str, style: str = 'smart') -> str:
    """
    Format a difference with character-level highlighting.
    
    Args:
        diff: Raw difference string from beet
        style: 'char' | 'word' | 'smart' | 'simple'
    
    Returns:
        Formatted string with character-level diff highlighting
    """
    from telegram.helpers import escape_markdown
    
    parsed = parse_and_format_difference(diff)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Case 1: Field change with old -> new values
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if parsed['type'] == 'field_change' and parsed['old_value'] and parsed['new_value']:
        field = escape_markdown(parsed['field'], version=2)
        old = parsed['old_value']
        new = parsed['new_value']
        
        # Apply diff based on style
        if style == 'char':
            old_fmt, new_fmt = char_diff(old, new)
        elif style == 'word':
            old_fmt, new_fmt = word_diff(old, new)
        elif style == 'smart':
            old_fmt, new_fmt = smart_diff(old, new, char_threshold=50)
        else:  # 'simple'
            old_fmt = escape_markdown(old, version=2)
            new_fmt = f"__*{escape_markdown(new, version=2)}*__"
        
        return f"  • 🔄 *{field}:* {old_fmt} → {new_fmt}"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Case 2: Field change with only new value (no old value to compare)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif parsed['type'] == 'field_change' and parsed['new_value']:
        field = escape_markdown(parsed['field'], version=2)
        new = escape_markdown(parsed['new_value'], version=2)
        return f"  • 🔄 *{field}:* __*{new}*__"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Case 3: Missing items
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif parsed['type'] == 'missing':
        field = escape_markdown(parsed['field'] or 'items', version=2)
        return f"  • ❌ *Missing {field}*"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Case 4: Extra/unmatched items
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif parsed['type'] == 'extra':
        field = escape_markdown(parsed['field'] or 'items', version=2)
        return f"  • ➕ *Extra {field}*"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Case 5: Generic mismatch
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif parsed['type'] == 'mismatch':
        field = escape_markdown(parsed['field'], version=2)
        return f"  • ⚠️ *{field} mismatch*"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Fallback: Generic difference
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    else:
        content = escape_markdown(parsed['raw'], version=2)
        return f"  • ⚠️ *{content}*"


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

    # MusicBrainz ID
    mb_id = extract_musicbrainz_id(cleaned)
    if mb_id:
        info['mb_id'] = mb_id
        info['mb_url'] = build_url_from_id('musicbrainz', mb_id)
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

def parse_verbose_candidates(output: str) -> List[Dict[str, Any]]:
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
    
    # Discogs candidates
    # Patterns:
    # - Candidate: Artist - Album (r1234567)
    # - Candidate: Artist - Album (m1234567)
    # - Candidate: Artist - Album (Discogs: 1234567)
    discogs_pattern = re.compile(
        r'Candidate:\s*(.+?)\s*\((?:Discogs:\s*)?([rm]\d+)\)\s*\n'
        r'.*?Distance:\s*([\d.]+)',
        re.DOTALL | re.IGNORECASE
    )
    
    for match in discogs_pattern.finditer(cleaned):
        full_title = match.group(1).strip()
        discogs_id = match.group(2).lower()
        distance = float(match.group(3))
        
        normalized = normalize_title(full_title)
        # Only add if not already mapped (MusicBrainz has priority)
        if normalized not in id_map:
            id_map[normalized] = ('discogs', discogs_id, distance)
            logger.debug(f"Found Discogs ID: {discogs_id} for '{full_title}' (distance: {distance})")

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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HIGH-LEVEL PARSE ENTRYPOINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_beet_output(stdout: str, stderr: str, path: str) -> Dict[str, Any]:
    """
    High-level parser: classify beet output and build the canonical import dict
    used by the bot. Always returns a dict with the schema defined above.
    Supports both MusicBrainz and Discogs sources.
    """
    raw = (stdout or "") + "\n" + (stderr or "")
    cleaned_low = clean_ansi_codes(raw).lower()

    now = datetime.datetime.utcnow().isoformat() + "Z"

    base = {
        'status': 'needs_input',
        'path': str(path),
        'has_multiple_candidates': False,
        'selected_index': None,
        'candidates': [],
        'single_match': None,
        'raw_output': raw,
        'timestamp': now
    }

    # Direct success
    if any(k in cleaned_low for k in ['successfully imported', 'already in library', 'imported and tagged']):
        base['status'] = 'success'
        return base

    # Single match (heuristic: presence of "match (" plus musicbrainz/discogs url/id)
    has_match = 'match (' in cleaned_low
    has_mb = 'musicbrainz.org' in cleaned_low or extract_musicbrainz_id(raw)
    has_discogs = 'discogs' in cleaned_low and extract_discogs_id(raw)
    has_candidates_section = 'candidates:' not in cleaned_low
    
    if has_match and (has_mb or has_discogs) and has_candidates_section:
        base['status'] = 'single_match'
        info = parse_beet_match_info(raw)
        
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
            'info_short': f"{info.get('artist') or 'Unknown'} - {info.get('album') or 'Unknown'}",
            'raw_output': raw
        }
        base['single_match'] = candidate
        return base

    # Multiple candidates (use verbose parser)
    if 'candidates:' in cleaned_low:
        logger.debug('Parsing multiple candidates...')
        candidates = parse_verbose_candidates(raw)
        
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