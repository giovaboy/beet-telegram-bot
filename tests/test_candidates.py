"""
Several candidates: parsing beet's numbered list + release IDs from the
verbose lines, and the candidates screen. Fixture: real beet >= 2.13 output.
"""
from pathlib import Path

import pytest

import ui.keyboards as keyboards
import ui.messages as ui_messages
from core.parsers import parse_beet_output, parse_verbose_candidates
from tests.markdown_v2 import markdown_v2_errors
from ui.keyboards import create_import_status_keyboard
from ui.messages import format_import_status

FIXTURE = Path(__file__).parent / "fixtures" / "multiple_candidates_real_barbirolli.txt"


@pytest.fixture
def output():
    return FIXTURE.read_text(encoding="utf-8").split("# ---\n", 1)[1]


@pytest.fixture
def result(output):
    return parse_beet_output(output, "", "/downloads/Barbirolli")


@pytest.fixture(autouse=True)
def _plugins(monkeypatch):
    monkeypatch.setattr(ui_messages, "has_discogs_plugin", lambda: True)
    monkeypatch.setattr(keyboards, "has_discogs_plugin", lambda: True)


def test_all_candidates_are_found(result):
    assert result['status'] == 'has_candidates'
    assert [c['number'] for c in result['candidates']] == list(range(1, 12))
    first = result['candidates'][0]
    assert first['similarity'] == 46.5 and first['source'] == 'musicbrainz'
    assert first['mb_id'] == '558268a5-de75-4725-8552-37069755d18a'
    assert (first['format'], first['year'], first['country'], first['label'], first['catalog_num']) == \
        ('Hybrid SACD', '2015', None, 'Pentatone', 'PTC 5186 504')
    assert first['differences'] == ['artist', 'album', 'year']


def test_release_ids_match_their_candidate(result):
    ids = {c['number']: c['mb_id'] or c['discogs_id'] for c in result['candidates']}
    assert ids[2] == 'd82ccddf-b559-40dc-97d4-1238d69bb3d8'
    assert ids[5] == 'r8131312'      # "David Oistrach - Johann Sebastian Bach · ..."
    assert ids[7] == 'r21039748'     # "Johann Sebastian Bach • ..."
    assert ids[8] == 'r12249813'     # "Johann Sebastian Bach · ... - David Oistrach - ..."
    assert ids[9] == 'r15755018'
    assert ids[10] == 'r26688413'
    assert ids[11] == '290f4b53-8292-470b-b669-5a3b9dda5b24'
    # Discogs *master* IDs printed while searching are never used as release IDs
    assert not {'r1391920', 'r3708390'} & set(ids.values())


def test_identical_titles_get_different_ids(result):
    elgar = [c for c in result['candidates'] if c['number'] in (3, 4)]
    assert elgar[0]['album'] == elgar[1]['album'] == 'Violin Concerto'
    assert elgar[0]['mb_id'] != elgar[1]['mb_id']
    assert {elgar[0]['country'], elgar[1]['country']} == {'JP', 'GB'}


def test_older_beet_list_format(output):
    old = output.replace("  1. 46.5% ", "  1. (46.5%) ").replace(
        ") from MusicBrainz", ")").replace(") from Discogs", ")")
    candidates = parse_verbose_candidates(old)
    assert len(candidates) == 11 and candidates[0]['similarity'] == 46.5
    assert candidates[0]['mb_id'] == '558268a5-de75-4725-8552-37069755d18a'
    assert candidates[4]['discogs_id'] == 'r8131312'


def test_ids_only_fallback():
    verbose = ("Candidate: A - B (558268a5-de75-4725-8552-37069755d18a) from MusicBrainz\n"
               "Success. Distance: 0.20\n"
               "Candidate: C - D (12249813) from Discogs\nSuccess. Distance: 0.10\n")
    candidates = parse_verbose_candidates(verbose)
    assert [(c['number'], c['similarity'], c['album']) for c in candidates] == [(1, 90.0, 'D'), (2, 80.0, 'B')]
    assert candidates[0]['discogs_id'] == 'r12249813'


def test_candidates_screen(result):
    msg = format_import_status(result)
    assert markdown_v2_errors(msg) == [] and len(msg) < 4000
    assert '11 candidates' in msg
    assert '📀 CD · 2010 · JP · RCA Red Seal · SICC 1331' in msg
    assert 'and 1 more candidates' in msg


def test_candidate_buttons_tell_releases_apart(result):
    keyboard = create_import_status_keyboard(result)
    labels = [row[0].text for row in keyboard.inline_keyboard[:10]]
    assert labels[2] == '3️⃣ 33% Violin Concerto · 2010 JP'
    assert labels[3] == '4️⃣ 33% Violin Concerto · 2010 GB'
    assert all(len(label) <= 60 for label in labels)   # emoji + % + album · year country
    callbacks = [row[0].callback_data for row in keyboard.inline_keyboard[:10]]
    assert callbacks[4] == 'match_4_r8131312'
