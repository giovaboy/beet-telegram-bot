"""
Security regression tests:
- path confinement inside IMPORT_PATH (symlinks, '..', sibling dirs)
- directory selection by snapshot (no index shifting between list and click)
"""
import os
from types import SimpleNamespace

import pytest

import core.utils as utils
import core.beet_manager as beet_manager
import handlers.callbacks as callbacks
from core.beet_manager import BeetImportManager


@pytest.fixture
def import_root(tmp_path, monkeypatch):
    """A fake IMPORT_PATH with a normal album, a nested album and some traps."""
    root = tmp_path / "downloads"
    (root / "Album A").mkdir(parents=True)
    (root / "Album A" / "01.flac").write_bytes(b"x")
    (root / "Collection" / "Album B").mkdir(parents=True)
    outside = tmp_path / "music"
    outside.mkdir()
    (tmp_path / "secret.txt").write_text("token")
    (tmp_path / "downloads2").mkdir()
    # Traps: a symlinked album dir and a symlinked "metadata" file
    os.symlink(outside, root / "linkdir")
    os.symlink(tmp_path / "secret.txt", root / "Album A" / "info.txt")

    for module in (utils, beet_manager):
        monkeypatch.setattr(module, "IMPORT_PATH", str(root))
    return root


@pytest.fixture
def manager():
    m = BeetImportManager.__new__(BeetImportManager)  # skip state loading
    m.current_import = None
    return m


# ── is_within_import_path ─────────────────────────────────────────────

def test_regular_file_is_within(import_root):
    assert utils.is_within_import_path(import_root / "Album A" / "01.flac")


def test_symlink_pointing_outside_is_rejected(import_root):
    assert not utils.is_within_import_path(import_root / "Album A" / "info.txt")


def test_dotdot_escape_is_rejected(import_root):
    assert not utils.is_within_import_path(import_root / ".." / "secret.txt")


def test_root_itself_only_when_allowed(import_root):
    assert not utils.is_within_import_path(import_root)
    assert utils.is_within_import_path(import_root, allow_root=True)


# ── BeetImportManager._validate_path ──────────────────────────────────

@pytest.mark.parametrize("rel, expected", [
    ("Album A", None),
    ("Collection/Album B", None),          # nested albums are fine
    ("linkdir", None),                     # link itself lives inside root
    ("", "error_root"),
    ("Album A/..", "error_root"),
    ("../music", "error_not_found"),
    ("../downloads2", "error_not_found"),  # prefix trick: /downloads2
    ("missing", "error_not_found"),
])
def test_validate_path(import_root, manager, rel, expected):
    path = os.path.join(str(import_root), rel) if rel else str(import_root)
    assert manager._validate_path(path) == expected


def test_delete_refuses_symlinked_dir(import_root, manager):
    result = manager.delete_directory(str(import_root / "linkdir"))
    assert result["status"] == "error"
    assert (import_root.parent / "music").is_dir()  # target untouched


def test_skip_refuses_path_outside_root(import_root, manager):
    result = manager.skip_item(str(import_root.parent / "music"))
    assert result == {"status": "error", "message": "error_not_found"}
    assert (import_root.parent / "music").is_dir()


# ── get_selected_dir uses the snapshot the user saw ───────────────────

def _context(dirs):
    return SimpleNamespace(user_data={"dir_list": [str(d) for d in dirs]})


def test_selection_is_stable_when_new_dirs_appear(import_root):
    shown = [import_root / "Album A", import_root / "Collection" / "Album B"]
    context = _context(shown)
    # A new download arrives after the list was shown: indexes must not shift
    (import_root / "New Download").mkdir()
    assert callbacks.get_selected_dir(context, 1) == shown[1]


def test_selection_out_of_range_or_missing(import_root):
    context = _context([import_root / "Album A"])
    assert callbacks.get_selected_dir(context, None) is None
    assert callbacks.get_selected_dir(context, 5) is None
    assert callbacks.get_selected_dir(SimpleNamespace(user_data={}), 0) is None


def test_selection_of_deleted_dir_returns_none(import_root):
    gone = import_root / "Gone"
    gone.mkdir()
    context = _context([gone])
    gone.rmdir()
    assert callbacks.get_selected_dir(context, 0) is None
