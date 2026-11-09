"""
MarkdownV2 view of a beet match (see core/match_details.py for the data).

Layout:
    🎯 Match 76.3% · MusicBrainz
    🎤 artist / 💿 album / 📀 media · year · label
    ⚖️ what beet penalized
    Album tags: current → new (word diff)
    Tracks: counters, missing / unmatched lists (always visible),
    then every changed track inside an expandable block quote.
"""
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from telegram.helpers import escape_markdown

from core.match_details import word_key
from i18n.translations import t

# Room for the match body inside one Telegram message (the caller adds a
# header, links and a question around it).
DEFAULT_BUDGET = 3300
MAX_LISTED_EXTRA = 10   # missing / unmatched tracks listed one by one
CONTEXT_WORDS = 3       # unchanged words kept around a change...
MAX_UNTRIMMED_DIFF = 100  # ...but only in diffs longer than this (characters)
FIELD_ICONS = {'Artist': '🎤', 'Album': '💿', 'Title': '🎵'}


def esc(value) -> str:
    return escape_markdown(str(value), version=2)


def shorten(text: str, limit: int) -> str:
    text = text or ''
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Diffs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _word_key(word: str) -> str:
    """Words equal up to case, accents, quotes and punctuation compare equal."""
    return word_key(word) or '·'  # punctuation-only tokens ("-", "–") are all alike


def inline_diff(old: str, new: str, context: int = CONTEXT_WORDS) -> str:
    """
    One-line word diff: ~removed~ *added*, unchanged words in plain text
    (shown as in the new title). Case/punctuation-only differences are not
    highlighted. Long unchanged runs are shortened to `context` words around
    each change.
    """
    a, b = (old or '').split(), (new or '').split()
    ops = SequenceMatcher(None, [_word_key(w) for w in a], [_word_key(w) for w in b],
                          autojunk=False).get_opcodes()
    # Shorten only long lines: in short ones the unchanged words (e.g. the
    # movement name of a classical track) are what tells tracks apart.
    removed = sum(len(' '.join(a[i1:i2])) for tag, i1, i2, _, _ in ops if tag in ('delete', 'replace'))
    shorten_runs = len(' '.join(b)) + removed > MAX_UNTRIMMED_DIFF
    parts = []
    for n, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == 'equal':
            words = b[j1:j2]
            first, last = n == 0, n == len(ops) - 1
            # Only shorten when it actually saves a few words
            if len(ops) == 1 or not shorten_runs:
                text = ' '.join(words)
            elif first and len(words) > context + 2:
                text = '… ' + ' '.join(words[-context:])
            elif last and len(words) > context + 2:
                text = ' '.join(words[:context]) + ' …'
            elif not first and not last and len(words) > 2 * context + 2:
                text = ' '.join(words[:context]) + ' … ' + ' '.join(words[-context:])
            else:
                text = ' '.join(words)
            parts.append(esc(text))
            continue
        if i2 > i1:
            parts.append(f"~{esc(' '.join(a[i1:i2]))}~")
        if j2 > j1:
            parts.append(f"*{esc(' '.join(b[j1:j2]))}*")
    return ' '.join(parts)


def field_diff(old: str, new: str) -> str:
    """Album-level value: word diff if similar, otherwise ~old~ → *new*."""
    ratio = SequenceMatcher(None, (old or '').casefold(), (new or '').casefold()).ratio()
    if ratio >= 0.5:
        return inline_diff(old, new)
    return f"~{esc(shorten(old, 80))}~ → *{esc(new)}*"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tracks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def track_line(track: Dict[str, Any]) -> str:
    kinds = track.get('kinds') or []
    if track['old_index'] == track['new_index']:
        index = esc(f"#{track['new_index']}")
    else:
        index = esc(f"#{track['old_index']}→#{track['new_index']}")

    if 'title' in kinds:
        icon, title = '📝', inline_diff(track['old_title'], track['new_title'])
    elif 'minor' in kinds:
        icon, title = '✏️', esc(shorten(track['new_title'], 70))
    elif 'index' in kinds:
        icon, title = '🔢', esc(shorten(track['new_title'], 70))
    else:
        icon, title = '⏱️', esc(shorten(track['new_title'], 70))

    line = f"{icon} {index} {title}"
    if 'length' in kinds:
        line += f" · ⏱️ {esc(track['old_length'])} → {esc(track['new_length'])}"
    return line


def _quote_lines(tracks: List[Dict[str, Any]]) -> List[str]:
    lines, medium = [], None
    for track in tracks:
        if track.get('medium') and track['medium'] != medium:
            medium = track['medium']
            lines.append(f"💿 *{esc(medium)}*")
        lines.append(track_line(track))
    return lines


def expandable_quote(lines: List[str]) -> str:
    """Telegram expandable block quote (collapsed by default)."""
    return '**>' + '\n>'.join(lines) + '||'


def tracks_quote(tracks: List[Dict[str, Any]], budget: int) -> str:
    """
    All changed tracks if they fit; otherwise case/punctuation-only changes
    are left out first (they are counted in the summary anyway), then the
    list is cut with an "...and N more" line.
    """
    def size(lines):
        return sum(len(line) + 2 for line in lines) + 5

    lines = _quote_lines(tracks)
    if size(lines) <= budget:
        return expandable_quote(lines)

    significant = [tr for tr in tracks if tr.get('kinds') != ['minor']]
    hidden_minor = len(tracks) - len(significant)
    lines = _quote_lines(significant)
    note = [t('match.minor_hidden', count=hidden_minor)] if hidden_minor else []

    if size(lines + note) > budget:
        kept, used = [], size(note) + 40
        for line in lines:
            if used + len(line) + 2 > budget:
                break
            kept.append(line)
            used += len(line) + 2
        shown = sum(1 for line in kept if not line.startswith('💿'))
        lines = kept + [t('match.more', count=len(significant) - shown)]
    return expandable_quote(lines + note) if lines or note else ''


def _extra_tracks_block(header: str, items: List[Dict[str, Any]]) -> List[str]:
    out = [header]
    for item in items[:MAX_LISTED_EXTRA]:
        length = f" \\({esc(item['length'])}\\)" if item.get('length') else ''
        out.append(f"  • {esc('#' + item['index'])} {esc(shorten(item['title'], 70))}{length}")
    if len(items) > MAX_LISTED_EXTRA:
        out.append('  ' + t('match.more', count=len(items) - MAX_LISTED_EXTRA))
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Whole match
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _similarity(value) -> str:
    if value is None:
        return '?'
    return f"{value:.1f}".rstrip('0').rstrip('.') if isinstance(value, float) else str(value)


def format_match_body(match: Dict[str, Any], budget: int = DEFAULT_BUDGET) -> Optional[str]:
    """
    MarkdownV2 description of a single match, or None when beet's output could
    not be parsed into details (the caller then uses the legacy rendering).
    """
    details = (match or {}).get('details') or {}
    if not details.get('found'):
        return None

    disambig = list(details.get('disambig') or [])
    source = match.get('source') or ''
    if disambig and disambig[0].lower() in ('musicbrainz', 'discogs'):
        source = disambig.pop(0)

    out = [t('match.header', similarity=esc(_similarity(details.get('similarity'))),
             source=esc(source or '?'))]
    out.append(f"🎤 {esc(details.get('artist') or match.get('artist') or '?')}")
    out.append(f"💿 {esc(details.get('album') or match.get('album') or '?')}")
    if disambig:
        out.append(f"📀 {esc(' · '.join(disambig))}")
    if details.get('penalties'):
        out.append(t('match.differs_in', fields=esc(', '.join(details['penalties']))))
    if details.get('wrapped'):
        out.append(t('match.wrapped_warning'))

    if details.get('album_changes'):
        out.append('')
        out.append(t('match.album_tags'))
        for change in details['album_changes']:
            icon = FIELD_ICONS.get(change['field'], '•')
            out.append(f"{icon} {field_diff(change['old'], change['new'])}")

    tracks = details.get('tracks') or []
    missing = details.get('missing')
    unmatched = details.get('unmatched')
    out.append('')
    if not tracks and not missing and not unmatched:
        out.append(t('match.tracks_ok'))
        return '\n'.join(out)

    out.append(t('match.tracks_header', count=len(tracks)))
    counts = {kind: sum(1 for tr in tracks if kind in tr.get('kinds', []))
              for kind in ('title', 'minor', 'length', 'index')}
    chips = [t(f'match.chip_{kind}', count=n) for kind, n in counts.items() if n]
    if chips:
        out.append(' · '.join(chips))
    if missing:
        out += _extra_tracks_block(
            t('match.missing', count=missing['count'], total=missing['total'],
              percent=esc(_similarity(missing['percent']))),
            missing['items'])
    if unmatched:
        out += _extra_tracks_block(t('match.unmatched', count=unmatched['count']), unmatched['items'])

    if tracks:
        used = sum(len(line) + 1 for line in out)
        out.append(tracks_quote(tracks, max(budget - used, 300)))
    return '\n'.join(out)
