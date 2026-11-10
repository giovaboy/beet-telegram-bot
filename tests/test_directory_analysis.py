"""
Directory analysis: audio formats beet can/can't import, single-file + CUE
images, and the warnings shown in the directory details.
"""
import pytest

import ui.messages as ui_messages
from core.directory_analyzer import analyze_directory, find_importable_albums
from tests.markdown_v2 import markdown_v2_errors
from ui.messages import format_directory_details


def _album(root, name, files):
    album = root / name
    for rel in files:
        path = album / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return album


@pytest.fixture
def root(tmp_path):
    return tmp_path / "downloads"


def test_more_formats_beet_supports_are_counted(root):
    album = _album(root, "Hi-res", ["01.dsf", "02.aiff", "03.wma", "04.mpc", "05.flac"])
    assert analyze_directory(str(album))['audio_count'] == 5


def test_unsupported_audio_is_listed_with_a_warning(root):
    album = _album(root, "Schumann - Symphonies", ["CD1/01.dff", "CD1/02.dff", "CD2/01.dff", "CD1/info.txt"])
    assert album in find_importable_albums(root)            # not silently missing
    structure = analyze_directory(str(album))
    assert structure['type'] == 'multi_disc'
    assert all(disc['audio_count'] == 0 for disc in structure['discs'])
    msg = format_directory_details(album.name, structure)
    assert "3 audio files beet cannot import" in msg and "\\.dff" in msg
    assert markdown_v2_errors(msg) == []


def test_unsupported_audio_only_folder_is_listed(root):
    album = _album(root, "SACD rip", ["disc.iso"])
    assert album in find_importable_albums(root)


@pytest.mark.parametrize("files, expected", [
    (["Signals.flac", "Signals.cue", "cover.jpg"], True),               # whole-disc image
    (["CD1/a.ape", "CD1/a.cue", "CD2/b.ape", "CD2/b.cue"], True),        # one image per disc
    ([f"{n:02d}.flac" for n in range(1, 9)] + ["album.cue"], False),     # already split, cue kept
    (["01.flac", "02.flac"], False),                                     # no cue
])
def test_cue_image_detection(root, files, expected):
    album = _album(root, "Album", files)
    structure = analyze_directory(str(album))
    parts = structure.get('discs') or [structure]
    assert any(part['cue_image'] for part in parts) is expected
    msg = format_directory_details(album.name, structure)
    assert ("CUE sheet" in msg) is expected
    assert markdown_v2_errors(msg) == []


def test_no_warnings_for_a_normal_album(root):
    album = _album(root, "Normal", ["01.flac", "02.flac", "cover.jpg"])
    msg = format_directory_details(album.name, analyze_directory(str(album)))
    assert "⚠️" not in msg


@pytest.fixture(autouse=True)
def _no_plugins(monkeypatch):
    monkeypatch.setattr(ui_messages, "has_discogs_plugin", lambda: False)
