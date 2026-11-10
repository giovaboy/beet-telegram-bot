"""
Startup checks: state files left owned by root (after switching the container
to PUID:PGID) must not make every click fail.
"""
import os

import pytest
from telegram.ext import PicklePersistence

import bot

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "PERSISTENCE_FILE", str(tmp_path / "beet_bot_persistence.pickle"))
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "beet_import_state.json"))
    yield tmp_path
    for f in tmp_path.iterdir():  # let pytest clean up
        f.chmod(0o600)


def test_writable_folder_uses_persistence(state_dir):
    assert bot.unusable_state_files([bot.PERSISTENCE_FILE, bot.STATE_FILE]) == []
    assert isinstance(bot.build_persistence(), PicklePersistence)


def test_foreign_owned_pickle_disables_persistence_with_clear_log(state_dir, caplog):
    pickle = state_dir / "beet_bot_persistence.pickle"
    pickle.write_bytes(b"")
    pickle.chmod(0o000)  # what a root-owned 0644/0600 file looks like to PUID
    assert bot.build_persistence() is None
    assert "chown -R" in caplog.text and "WITHOUT persistence" in caplog.text


def test_unwritable_import_state_is_reported(state_dir, caplog):
    state = state_dir / "beet_import_state.json"
    state.write_text("{}")
    state.chmod(0o400)
    assert isinstance(bot.build_persistence(), PicklePersistence)  # persistence still fine
    assert "beet_import_state.json" in caplog.text

