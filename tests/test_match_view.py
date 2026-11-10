"""
Match details: parsing of beet's match block and its Telegram rendering.

Fixtures single_match_mb_details_*.txt were rendered by beets 2.14.1's own
display code (see their header), in the layout the bot now requests
(NO_COLOR=1, COLUMNS=2000) and in beet's default wrapped layout.
"""
from pathlib import Path

import pytest

import core.beet_manager as beet_manager
import ui.messages as ui_messages
from core.match_details import classify_track, length_seconds, normalize_title, parse_match_details
from core.parsers import clean_ansi_codes, parse_beet_output
from tests.markdown_v2 import markdown_v2_errors
from ui.match_view import expandable_quote, format_match_body, group_line, group_tracks, inline_diff, tracks_quote
from ui.messages import format_import_status

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text.split("# ---\n", 1)[1]


@pytest.fixture
def single_line():
    return parse_match_details(clean_ansi_codes(_fixture("single_match_mb_details_single_line.txt")))


@pytest.fixture
def wrapped():
    return parse_match_details(clean_ansi_codes(_fixture("single_match_mb_details_wrapped.txt")))


@pytest.fixture(autouse=True)
def no_discogs(monkeypatch):
    monkeypatch.setattr(ui_messages, "has_discogs_plugin", lambda: False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_header(single_line):
    d = single_line
    assert d['found'] and d['similarity'] == 32.0
    assert (d['artist'], d['album']) == ("Christian Ferras", "The SWR Recordings")
    assert d['penalties'] == ['artist', 'album', 'tracks', 'data source']
    assert d['disambig'] == ['MusicBrainz', '2xCD', '2022', 'SWR Classic']
    assert d['url'].endswith('10e48a26-dee1-4438-a9de-427a63a38e34')
    assert d['wrapped'] is False


def test_album_changes_are_complete(single_line):
    changes = {c['field']: c for c in single_line['album_changes']}
    assert changes['Artist']['old'] == "Christian Ferras, Pierre Barbizet, Stuttgart Radio Symphony Orchestra"
    assert changes['Album']['old'] == "Beethoven, Tchaikovsky & Others: Violin Sonatas & Concertos"
    assert changes['Album']['new'] == "The SWR Recordings"


def test_tracks_have_full_titles_and_media(single_line):
    tracks = single_line['tracks']
    assert len(tracks) == 6
    assert tracks[0]['medium'] == 'CD 1' and tracks[4]['medium'] == 'CD 2'
    assert tracks[0]['old_title'] == 'Violin Sonata No. 9 in A Major, Op. 47 "Kreutzer": I. Adagio sostenuto - Presto'
    assert tracks[0]['new_title'] == 'Violin Sonata no. 9 in A, op. 47 “Kreutzer”: I. Adagio sostenuto – Presto'
    assert tracks[5]['kinds'] == ['title', 'length']
    assert (tracks[5]['old_length'], tracks[5]['new_length']) == ('19:13', '19:50')


def test_missing_and_unmatched_tracks(single_line):
    missing, unmatched = single_line['missing'], single_line['unmatched']
    assert (missing['count'], missing['total'], missing['percent']) == (2, 8, 25.0)
    assert [i['index'] for i in missing['items']] == ['7', '8']
    assert missing['items'][1]['title'] == 'Violin Concerto in D, op. 35: III. Finale. Allegro vivacissimo'
    assert unmatched['count'] == 1
    assert unmatched['items'][0] == {'title': 'Bonus: Interview with Christian Ferras', 'index': '7', 'length': '5:12'}


def test_wrapped_output_is_flagged(wrapped):
    assert wrapped['wrapped'] is True
    assert len(wrapped['tracks']) == 6                  # first line of each change still parsed
    assert wrapped['missing']['count'] == 2             # these lines are never wrapped
    assert wrapped['unmatched']['count'] == 1


def test_parse_beet_output_exposes_details():
    result = parse_beet_output(_fixture("single_match_mb_details_single_line.txt"), "", "/downloads/x")
    assert result['status'] == 'single_match'
    assert result['single_match']['details']['missing']['count'] == 2


def test_no_match_block():
    assert parse_match_details("No matching release found for 3 tracks.")['found'] is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Classification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _track(old_title="A", new_title="A", old_len="3:00", new_len="3:00", old_index="1", new_index="1"):
    return dict(old_title=old_title, new_title=new_title, old_length=old_len, new_length=new_len,
                old_index=old_index, new_index=new_index)


@pytest.mark.parametrize("track, kinds", [
    (_track('Sonata No. 9 "Kreutzer"', 'Sonata no. 9 “Kreutzer”'), ['minor']),
    (_track('Prelude - Fugue', 'Prélude – Fugue'), ['minor']),
    (_track('Tzigane, M. 76', 'Tzigane'), ['title']),
    (_track('I. Allegro ma non troppo', 'I. Allegro, ma non troppo'), ['minor']),
    (_track('Concerto (Live)', 'Concerto'), ['title']),
    (_track(old_len='3:00', new_len='3:09'), []),           # below beet's 10 s threshold
    (_track(old_len='3:00', new_len='3:10'), ['length']),
    (_track(old_index='1', new_index='2'), ['index']),
])
def test_classify_track(track, kinds):
    assert classify_track(track) == kinds


def test_helpers():
    assert length_seconds('11:36') == 696 and length_seconds('1:02:03') == 3723
    assert normalize_title('“Kreutzer ”: I.') == normalize_title('"Kreutzer": i.')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Diffs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_inline_diff_ignores_case_and_punctuation_noise():
    diff = inline_diff('Violin Sonata No. 9 in A Major, Op. 47 "Kreutzer": I. Adagio',
                       'Violin Sonata no. 9 in A, op. 47 “Kreutzer”: I. Adagio')
    assert diff == 'Violin Sonata no\\. 9 in A, ~Major,~ op\\. 47 “Kreutzer”: I\\. Adagio'


def test_inline_diff_replacement_and_trimming():
    assert inline_diff('Tzigane, M. 76', 'Tzigane') == 'Tzigane ~M\\. 76~'
    assert inline_diff('A b', 'A b c') == 'A b __*c*__'   # additions: bold + underline
    old = 'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen ' \
          'seventeen eighteen nineteen twenty OLD twentyone twentytwo'
    new = old.replace('OLD', 'NEW')
    diff = inline_diff(old, new)
    assert diff.startswith('… ') and '~OLD~ __*NEW*__' in diff  # long line: leading words shortened


def test_strikethrough_uses_single_tilde():
    assert inline_diff('a b', 'a') == 'a ~b~'   # MarkdownV2: ~x~, not ~~x~~


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Rendering
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.parametrize("name", ["single_line", "wrapped"])
def test_status_message_is_valid(name):
    result = parse_beet_output(_fixture(f"single_match_mb_details_{name}.txt"), "", "/downloads/x")
    msg = format_import_status(result)
    assert markdown_v2_errors(msg) == []
    assert len(msg) < 4000
    assert '**>' in msg and msg.count('||') == 1          # tracks in an expandable quote
    assert 'Missing from your files: 2/8' in msg
    assert ('wraps long lines' in msg) == (name == 'wrapped')


def test_status_without_details_shows_summary():
    result = {'status': 'single_match', 'path': '/downloads/x', 'single_match': {
        'artist': 'A', 'album': 'B', 'year': '1999', 'similarity': 90.0, 'source': 'musicbrainz',
        'differences': ['≠ album: B -> C'], 'mb_url': None}}
    msg = format_import_status(result)
    assert 'Match 90%' in msg and '🎤 A' in msg and '📅 1999' in msg and '**>' not in msg
    assert markdown_v2_errors(msg) == []


def test_body_fits_budget_for_huge_albums():
    tracks = []
    for n in range(1, 151):
        old = f'Track number {n} with a fairly long original title, Op. {n} No. 1'
        new = old.replace('Op.', 'op.') if n % 2 else old + ' (remastered)'
        track = _track(old, new, old_index=str(n), new_index=str(n))
        track['kinds'] = classify_track(track)
        track['medium'] = f'CD {1 + n // 50}'
        tracks.append(track)
    body = format_match_body({'details': {
        'found': True, 'similarity': 80.0, 'artist': 'X', 'album': 'Y', 'tracks': tracks,
        'disambig': ['MusicBrainz'], 'penalties': ['tracks']}}, budget=3300)
    assert len(body) <= 3300
    assert body.endswith('||') and markdown_v2_errors(body) == []
    assert 'case/punctuation changes only' in body       # minor ones dropped first
    assert '…and' in body                                 # then the list is cut


def test_tracks_quote_keeps_everything_when_it_fits():
    track = _track('A b', 'A c')
    track['kinds'] = classify_track(track)
    assert tracks_quote([track], 1000) == expandable_quote(['📝 \\#1 A ~b~ __*c*__'])


def test_no_details_returns_summary():
    assert format_match_body({'artist': 'A'}).startswith('📊 *Match ?%*')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# How beet is run
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_docker_command_requests_unwrapped_uncolored_output(monkeypatch):
    monkeypatch.setattr(beet_manager, "BEET_CONTAINER", "beets")
    monkeypatch.setattr(beet_manager, "BEET_USER", "abc")
    manager = beet_manager.BeetImportManager.__new__(beet_manager.BeetImportManager)
    cmd = manager._build_command(["beet", "import", "/downloads/x"], interactive=True)
    assert cmd == ["docker", "exec", "-i", "-u", "abc", "-e", "NO_COLOR=1", "-e", "COLUMNS=2000",
                   "beets", "beet", "import", "/downloads/x"]


def test_preview_uses_detailed_view():
    from handlers.flows import preview_text
    preview = parse_beet_output(_fixture("single_match_mb_details_single_line.txt"), "", "/downloads/x")
    text = preview_text({'status': 'needs_confirmation', 'preview': preview}, 'mb')
    assert text.startswith('🔎 *Import preview*')
    assert 'Missing from your files: 2/8' in text and 'View on MusicBrainz' in text
    assert markdown_v2_errors(text) == [] and len(text) < 4000


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Real output from the server (beet >= 2.13, run by the bot)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def real():
    return parse_beet_output(_fixture("single_match_mb_real_ferras.txt"), "", "/downloads/Christian Ferras")


def test_real_output_is_parsed_completely(real):
    details = real['single_match']['details']
    assert real['status'] == 'single_match' and details['similarity'] == 76.3
    assert details['wrapped'] is False and details['missing'] is None
    assert [t['medium'] for t in details['tracks']].count('CD 4') == 5
    assert len(details['tracks']) == 29
    kinds = {t['new_index']: t['kinds'] for t in details['tracks']}
    assert kinds['19'] == ['minor']   # only a comma added
    assert kinds['7'] == ['title']    # "M. 76" removed
    assert kinds['8'] == ['title']    # "(Spring)" added


def test_every_retitled_track_has_a_visible_change(real):
    for track in real['single_match']['details']['tracks']:
        diff = inline_diff(track['old_title'], track['new_title'])
        highlighted = '~' in diff or '*' in diff
        assert highlighted == ('title' in track['kinds']), (track['new_index'], diff)


def test_real_status_message(real):
    msg = format_import_status(real)
    assert markdown_v2_errors(msg) == [] and len(msg) < 4000
    assert '📝 20 retitled · ✏️ 9 case/punctuation only' in msg



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Grouping movements of the same work with the same change
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _movement(n, old, new, medium='CD 1', old_len='3:00', new_len='3:00'):
    track = _track(old, new, old_len, new_len, str(n), str(n))
    track['kinds'] = classify_track(track)
    track['medium'] = medium
    return track


def test_real_album_is_grouped(real):
    tracks = real['single_match']['details']['tracks']
    groups = [[tr['new_index'] for tr in g] for g in group_tracks(tracks)]
    assert groups == [['1'], ['2'], ['3'], ['4', '5', '6'], ['7'], ['8', '9', '10', '11'],
                      ['12', '13', '14'], ['15'], ['16', '17', '18'], ['19', '20', '21'],
                      ['22', '23', '24'], ['25', '26', '27'], ['28'], ['29']]
    msg = format_import_status(real)
    assert '📝 \\#8–11 Violin Sonata No\\. 5 in F major, Op\\. 24 __*\\(Spring\\):*__ …' in msg
    assert '✏️ \\#16–18 Violin Sonata No\\. 2 in D minor, Op\\. 121: …' in msg
    assert markdown_v2_errors(msg) == []


def test_group_hides_movement_names_even_when_change_is_at_the_end():
    group = [_movement(1, 'Concerto X: I. Andante (Live)', 'Concerto X: I. Andante'),
             _movement(2, 'Concerto X: II. Allegro molto (Live)', 'Concerto X: II. Allegro molto')]
    assert len(group_tracks(group)) == 1
    assert group_line(group) == '📝 \\#1–2 Concerto X: … ~\\(Live\\)~'


@pytest.mark.parametrize("second", [
    _movement(3, 'Concerto X: II. Adagio (Live)', 'Concerto X: II. Adagio'),        # #2 missing in between
    _movement(2, 'Concerto Y: II. Adagio (Live)', 'Concerto Y: II. Adagio'),        # another work
    _movement(2, 'Concerto X: II. Adagio (Live)', 'Concerto X: II. Adagio', medium='CD 2'),
    _movement(2, 'Concerto X: II. Adagio (Live)', 'Concerto X: II. Adagio', new_len='3:30'),  # length
    _movement(2, 'Concerto X: II. Adagio (Studio)', 'Concerto X: II. Adagio'),      # other change
])
def test_tracks_that_are_not_grouped(second):
    first = _movement(1, 'Concerto X: I. Andante (Live)', 'Concerto X: I. Andante')
    assert len(group_tracks([first, second])) == 2
