"""
MarkdownV2 view of a beet match (see core/match_details.py for the data).

Layout:
    📊 Match 76.3% · MusicBrainz
    🎤 artist / 💿 album / 📀 media · year · label
    ⚖️ what beet penalized
    Album tags: current → new (word diff)
    Tracks: counters, missing / unmatched lists (always visible),
    then every changed track inside an expandable block quote; movements of
    the same work with the same change are shown as one line ("#8–11 …").
"""
import re
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


def _word_ops(a: List[str], b: List[str]):
    return SequenceMatcher(None, [_word_key(w) for w in a], [_word_key(w) for w in b],
                           autojunk=False).get_opcodes()


def added(text: str) -> str:
    """Added words: bold + underline (bold alone is hard to spot on dark themes)."""
    return f"__*{esc(text)}*__"


def removed(text: str) -> str:
    return f"~{esc(text)}~"


def _equal_words(b: List[str], j1: int, j2: int, vary) -> List[str]:
    """Unchanged words; those in the `vary` range collapse into a single '…'."""
    words = []
    for j in range(j1, j2):
        if vary and vary[0] <= j < vary[1]:
            if not words or words[-1] != '…':
                words.append('…')
        else:
            words.append(b[j])
    return words


def inline_diff(old: str, new: str, context: int = CONTEXT_WORDS, vary=None) -> str:
    """
    One-line word diff: ~removed~ __*added*__, unchanged words in plain text
    (shown as in the new title). Case/punctuation-only differences are not
    highlighted. Long unchanged runs are shortened to `context` words around
    each change.

    `vary` is a (start, end) range of word positions in `new` that differ
    between the tracks of a group (the movement names): shown as one '…'.
    """
    a, b = (old or '').split(), (new or '').split()
    ops = _word_ops(a, b)
    # Shorten only long lines: in short ones the unchanged words (e.g. the
    # movement name of a classical track) are what tells tracks apart.
    removed_len = sum(len(' '.join(a[i1:i2])) for tag, i1, i2, _, _ in ops if tag in ('delete', 'replace'))
    shown_len = len(' '.join(_equal_words(b, 0, len(b), vary)))  # after collapsing `vary`
    shorten_runs = shown_len + removed_len > MAX_UNTRIMMED_DIFF
    parts = []
    for n, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == 'equal':
            words = _equal_words(b, j1, j2, vary)
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
            parts.append(esc(re.sub(r'…(?: …)+', '…', text)))
            continue
        if i2 > i1:
            parts.append(removed(' '.join(a[i1:i2])))
        if j2 > j1:
            parts.append(added(' '.join(b[j1:j2])))
    return ' '.join(part for part in parts if part)


def field_diff(old: str, new: str) -> str:
    """Album-level value: word diff if similar, otherwise ~old~ → new."""
    ratio = SequenceMatcher(None, (old or '').casefold(), (new or '').casefold()).ratio()
    if ratio >= 0.5:
        return inline_diff(old, new)
    return f"{removed(shorten(old, 80))} → {added(new)}"


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
        icon, title = '✏️', esc(track['new_title'])
    elif 'index' in kinds:
        icon, title = '🔢', esc(track['new_title'])
    else:
        icon, title = '⏱️', esc(track['new_title'])

    line = f"{icon} {index} {title}"
    if 'length' in kinds:
        line += f" · ⏱️ {esc(track['old_length'])} → {esc(track['new_length'])}"
    return line


# ── Grouping movements of the same work with the same change ─────────

def _work(title: str) -> Optional[str]:
    """'Violin Sonata No. 5, Op. 24: II. Adagio' → 'Violin Sonata No. 5, Op. 24'."""
    return title.rsplit(': ', 1)[0] if ': ' in title else None


def _number(track: Dict[str, Any]) -> Optional[int]:
    try:
        return int(track['new_index'])
    except (TypeError, ValueError):
        return None


def change_signature(track: Dict[str, Any]):
    """What changed, independently of the words that stayed the same.
    None for tracks that must always be shown alone (length/number changes)."""
    kinds = track.get('kinds') or []
    if kinds == ['minor']:
        return ('minor',)
    if kinds != ['title']:
        return None
    a, b = track['old_title'].split(), track['new_title'].split()
    return tuple(
        (tag, tuple(_word_key(w) for w in a[i1:i2]), tuple(_word_key(w) for w in b[j1:j2]))
        for tag, i1, i2, j1, j2 in _word_ops(a, b) if tag != 'equal'
    )


def _can_join(group: List[Dict[str, Any]], track: Dict[str, Any]) -> bool:
    first, prev = group[0], group[-1]
    signature = change_signature(track)
    work = _work(track['new_title'])
    return (
        signature is not None
        and signature == change_signature(first)
        and track.get('medium') == prev.get('medium')
        and _number(prev) is not None and _number(track) == _number(prev) + 1
        and work is not None and work == _work(first['new_title'])
    )


def group_tracks(tracks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Consecutive movements of the same work with exactly the same change."""
    groups: List[List[Dict[str, Any]]] = []
    for track in tracks:
        if groups and _can_join(groups[-1], track):
            groups[-1].append(track)
        else:
            groups.append([track])
    return groups


def _common_affixes(word_lists: List[List[str]]):
    shortest = min(len(words) for words in word_lists)
    prefix = 0
    while prefix < shortest and len({words[prefix] for words in word_lists}) == 1:
        prefix += 1
    suffix = 0
    while suffix < shortest - prefix and len({words[-1 - suffix] for words in word_lists}) == 1:
        suffix += 1
    return prefix, suffix


def group_line(group: List[Dict[str, Any]]) -> str:
    if len(group) == 1:
        return track_line(group[0])
    first = group[0]
    index = esc(f"#{first['new_index']}–{group[-1]['new_index']}")
    words = [track['new_title'].split() for track in group]
    prefix, suffix = _common_affixes(words)
    vary = (prefix, len(words[0]) - suffix)
    if first['kinds'] == ['minor']:
        return f"✏️ {index} {inline_diff(first['new_title'], first['new_title'], vary=vary)}"
    return f"📝 {index} {inline_diff(first['old_title'], first['new_title'], vary=vary)}"


def _quote_entries(tracks: List[Dict[str, Any]]):
    """(line, number of tracks it covers) — medium headers cover none."""
    entries, medium = [], None
    for group in group_tracks(tracks):
        if group[0].get('medium') and group[0]['medium'] != medium:
            medium = group[0]['medium']
            entries.append((f"💿 *{esc(medium)}*", 0))
        entries.append((group_line(group), len(group)))
    return entries


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

    entries = _quote_entries(tracks)
    lines = [line for line, _ in entries]
    if size(lines) <= budget:
        return expandable_quote(lines)

    significant = [tr for tr in tracks if tr.get('kinds') != ['minor']]
    hidden_minor = len(tracks) - len(significant)
    entries = _quote_entries(significant)
    lines = [line for line, _ in entries]
    note = [t('match.minor_hidden', count=hidden_minor)] if hidden_minor else []

    if size(lines + note) > budget:
        kept, used, shown = [], size(note) + 40, 0
        for line, count in entries:
            if used + len(line) + 2 > budget:
                break
            kept.append(line)
            used += len(line) + 2
            shown += count
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


def _summary_only(match: Dict[str, Any]) -> str:
    """When beet's match block could not be parsed: what the summary parser found."""
    out = [t('match.header', similarity=esc(_similarity(match.get('similarity'))),
             source=esc(match.get('source') or '?'))]
    out.append(f"🎤 {esc(match.get('artist') or '?')}")
    out.append(f"💿 {esc(match.get('album') or '?')}")
    if match.get('year'):
        out.append(f"📅 {esc(match['year'])}")
    return '\n'.join(out)


def format_match_body(match: Dict[str, Any], budget: int = DEFAULT_BUDGET) -> str:
    """MarkdownV2 description of a single match."""
    match = match or {}
    details = match.get('details') or {}
    if not details.get('found'):
        return _summary_only(match)

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
