"""
Panel lifecycle tests: one live message with a keyboard, stale buttons are
rejected, long operations do not leave clickable leftovers.

A FakeBot stands in for Telegram: it stores messages, applies edits and,
like Telegram, rejects MarkdownV2 with unescaped reserved characters.
"""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

import core.utils as utils
import handlers.callbacks as callbacks
import handlers.commands as commands
import handlers.flows as flows
import handlers.messages as messages
import ui.keyboards as keyboards
import ui.messages as ui_messages
from ui import panel
from tests.markdown_v2 import markdown_v2_errors

CHAT = 42
MB_ID = "11111111-2222-3333-4444-555555555555"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fakes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FakeBot:
    def __init__(self):
        self.messages = {}       # id -> dict(text, markup, kind)
        self.next_id = 100
        self.parse_errors = []   # texts Telegram would have rejected

    def _check(self, text, parse_mode):
        if parse_mode == 'MarkdownV2':
            errors = markdown_v2_errors(text)
            if errors:
                self.parse_errors.append((text, errors))
                raise BadRequest(f"Can't parse entities: reserved character {errors[0][1]!r}")

    def _new(self, kind, text=None, markup=None):
        self.next_id += 1
        self.messages[self.next_id] = {'text': text, 'markup': markup, 'kind': kind}
        return SimpleNamespace(message_id=self.next_id)

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
        self._check(text, parse_mode)
        return self._new('text', text, reply_markup)

    async def edit_message_text(self, chat_id, message_id, text, parse_mode=None, reply_markup=None, **kw):
        if message_id not in self.messages:
            raise BadRequest("Message to edit not found")
        self._check(text, parse_mode)
        msg = self.messages[message_id]
        if msg['text'] == text and msg['markup'] == reply_markup:
            raise BadRequest("Message is not modified: specified new message content is the same")
        msg.update(text=text, markup=reply_markup)

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None, **kw):
        if message_id not in self.messages:
            raise BadRequest("Message to edit not found")
        msg = self.messages[message_id]
        if msg['markup'] == reply_markup:
            raise BadRequest("Message is not modified")
        msg['markup'] = reply_markup

    async def delete_message(self, chat_id, message_id):
        if self.messages.pop(message_id, None) is None:
            raise BadRequest("Message to delete not found")

    async def send_photo(self, chat_id, photo, caption=None, **kw):
        return self._new('photo', caption)

    async def send_document(self, chat_id, document, filename=None, caption=None, **kw):
        return self._new('document', caption)

    # helpers for assertions
    def with_keyboard(self):
        return [mid for mid, m in self.messages.items() if m['markup'] is not None]

    def callbacks(self, message_id):
        markup = self.messages[message_id]['markup']
        return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]

    def button(self, message_id, action):
        """Stamped callback_data of the button whose action is `action`."""
        for data in self.callbacks(message_id):
            if panel.parse_callback(data)[1] == action:
                return data
        raise AssertionError(f"No button {action!r} in {self.callbacks(message_id)}")


class FakeManager:
    def __init__(self, dirs):
        self.dirs = dirs
        self.current_import = None
        self.calls = []

    def get_import_directories(self):
        return list(self.dirs)

    def save_state(self):
        pass

    def clear_state(self):
        self.current_import = None

    def start_import(self, path):
        self.calls.append(('start_import', path))
        return {
            'status': 'has_candidates', 'path': path, 'single_match': None,
            'candidates': [
                {'artist': 'Rush', 'album': 'Presto', 'year': '1989', 'similarity': 91.5, 'mb_id': MB_ID},
                {'artist': 'Rush', 'album': 'Presto (Remaster)', 'year': '2004', 'similarity': 80.0,
                 'discogs_id': 'r123456'},
            ],
        }

    def import_with_id(self, path, id=None, auto_apply=False):
        self.calls.append(('import_with_id', id, auto_apply))
        if auto_apply:
            return {'status': 'success', 'message': 'ok', 'output': ''}
        return {'status': 'needs_confirmation', 'output': '', 'preview': {'single_match': {
            'artist': 'Rush', 'album': 'Presto', 'year': '1989', 'similarity': 97.2,
            'differences': ['≠ album: Presto (Remaster) -> Presto'],
            'mb_url': f'https://musicbrainz.org/release/{MB_ID}',
        }}}

    def import_as_is(self, path):
        self.calls.append(('import_as_is', path))
        return {'status': 'error', 'message': 'beet exploded', 'output': ''}

    def skip_item(self, path):
        self.calls.append(('skip_item', path))
        return {'status': 'success', 'message': 'Moved to Presto'}

    def delete_directory(self, path):
        self.calls.append(('delete_directory', path))
        return {'status': 'success', 'message': os.path.basename(path)}


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    albums = []
    for name in ("Rush - Presto", "Other Album"):
        album = root / name
        album.mkdir(parents=True)
        (album / "01.flac").write_bytes(b"x" * 10)
        (album / "cover.jpg").write_bytes(b"jpg")
        albums.append(album)
    (tmp_path / "secret.txt").write_text("token")
    os.symlink(tmp_path / "secret.txt", albums[0] / "back.jpg")  # trap

    for module in (utils, flows, keyboards):
        monkeypatch.setattr(module, "IMPORT_PATH", str(root))
    monkeypatch.setattr(commands, "TELEGRAM_CHAT_ID", str(CHAT))
    monkeypatch.setattr(keyboards, "has_discogs_plugin", lambda: True)
    monkeypatch.setattr(ui_messages, "has_discogs_plugin", lambda: True)

    bot = FakeBot()
    context = SimpleNamespace(bot=bot, user_data={})
    manager = FakeManager(albums)
    yield SimpleNamespace(bot=bot, context=context, manager=manager, albums=albums, root=root)
    # Every text the bot sent must have been accepted by Telegram
    assert bot.parse_errors == [], bot.parse_errors


def _update(text=None):
    message = SimpleNamespace(text=text, reply_text=AsyncMock(), chat_id=CHAT)
    return SimpleNamespace(effective_chat=SimpleNamespace(id=CHAT), effective_user=SimpleNamespace(id=1),
                           message=message, callback_query=None)


async def click(env, data, message_id=None):
    """Simulate pressing a button; returns the mocked query.answer."""
    message_id = message_id or panel.panel_message_id(env.context)
    query = SimpleNamespace(
        data=data, answer=AsyncMock(),
        message=SimpleNamespace(message_id=message_id, chat=SimpleNamespace(id=CHAT)),
    )
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=CHAT),
                             effective_user=SimpleNamespace(id=1))
    await callbacks.button_callback(update, env.context, env.manager)
    return query.answer


async def press(env, action):
    """Press the button `action` on the live panel."""
    pid = panel.panel_message_id(env.context)
    return await click(env, env.bot.button(pid, action), pid)


def live(env):
    return env.bot.messages[panel.panel_message_id(env.context)]


def assert_single_keyboard(env):
    with_kb = env.bot.with_keyboard()
    assert with_kb in ([], [panel.panel_message_id(env.context)]), with_kb


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Panel mechanics
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_stamp_and_parse():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("a", callback_data="import_3"),
         InlineKeyboardButton("web", url="https://example.org")],
        [InlineKeyboardButton("too long", callback_data="x" * 60)],
    ])
    stamped = panel.stamp(markup, "abc123")
    assert stamped.inline_keyboard[0][0].callback_data == "abc123|import_3"
    assert stamped.inline_keyboard[0][1].url == "https://example.org"
    assert len(stamped.inline_keyboard) == 1  # over-long button dropped, not sent broken
    assert panel.parse_callback("abc123|match_1_abcd") == ("abc123", "match_1_abcd")
    assert panel.parse_callback("legacy_button") == (None, "legacy_button")


def test_fit_truncates_at_line_break():
    text = "\n".join(f"line {i}" for i in range(2000))
    fitted = panel.fit(text, limit=500)
    assert len(fitted) <= 500 and fitted.endswith("_…_")


@pytest.mark.asyncio
async def test_show_modes(env):
    ctx, bot = env.context, env.bot
    first = await panel.show(ctx, CHAT, "one", InlineKeyboardMarkup([[InlineKeyboardButton("x", callback_data="x")]]))
    assert await panel.show(ctx, CHAT, "two") == first                     # EDIT: same message
    bottom = await panel.show(ctx, CHAT, "three", mode=panel.BOTTOM)
    assert bottom != first and first not in bot.messages                  # BOTTOM: old deleted
    kept = await panel.show(ctx, CHAT, "four", InlineKeyboardMarkup([[InlineKeyboardButton("y", callback_data="y")]]),
                            mode=panel.KEEP)
    assert bot.messages[bottom]['markup'] is None and kept in bot.messages  # KEEP: old kept, inert


@pytest.mark.asyncio
async def test_show_recovers_when_panel_was_deleted(env):
    first = await panel.show(env.context, CHAT, "one")
    del env.bot.messages[first]  # user deleted it
    second = await panel.show(env.context, CHAT, "two")
    assert second != first and env.bot.messages[second]['text'] == "two"


@pytest.mark.asyncio
async def test_invalid_markup_falls_back_to_plain_text(env):
    mid = await panel.show(env.context, CHAT, "broken. *text*")
    assert env.bot.messages[mid]['text'] == "broken. *text*"  # sent anyway, as plain text
    assert len(env.bot.parse_errors) == 1
    env.bot.parse_errors.clear()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Full flow: one panel, edited in place
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_happy_path_uses_one_message(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    pid = panel.panel_message_id(env.context)
    assert "Directories to import" in live(env)['text']

    await press(env, "import_0")
    assert "Presto" in live(env)['text']
    await press(env, "start_import_0")
    assert env.manager.current_import['status'] == 'has_candidates'
    assert any(cb.endswith(f"match_0_{MB_ID[:8]}") for cb in env.bot.callbacks(pid))

    await press(env, f"match_0_{MB_ID[:8]}")
    assert "Import preview" in live(env)['text']
    await press(env, "confirm_import")

    assert ('import_with_id', MB_ID, True) in env.manager.calls
    assert env.manager.current_import is None
    assert "Import completed" in live(env)['text']
    assert panel.panel_message_id(env.context) == pid       # everything happened in ONE message
    assert len(env.bot.messages) == 1
    assert_single_keyboard(env)
    assert env.bot.parse_errors == []


@pytest.mark.asyncio
async def test_open_list_after_outcome_keeps_history(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    await press(env, "import_0")
    await press(env, "start_import_0")
    await press(env, "cancel_import")
    outcome = panel.panel_message_id(env.context)
    await press(env, "open_list")
    assert env.bot.messages[outcome]['markup'] is None       # outcome kept, inert
    assert "cancelled" in env.bot.messages[outcome]['text']
    assert panel.panel_message_id(env.context) != outcome
    assert_single_keyboard(env)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Stale buttons
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_double_click_runs_once(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    await press(env, "import_0")
    pid = panel.panel_message_id(env.context)
    data = env.bot.button(pid, "start_import_0")

    await click(env, data, pid)
    answer = await click(env, data, pid)  # second click, queued with the old token

    assert [c for c in env.manager.calls if c[0] == 'start_import'] == [('start_import', str(env.albums[0]))]
    answer.assert_awaited_once()
    assert "no longer active" in answer.await_args.args[0]
    assert env.bot.messages[pid]['markup'] is not None  # live panel keyboard untouched


@pytest.mark.asyncio
async def test_button_on_old_message_is_rejected_and_stripped(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    old = panel.panel_message_id(env.context)
    old_data = env.bot.button(old, "import_0")
    # /list again → new panel at the bottom; simulate Telegram failing to delete the old one
    env.bot.delete_message = AsyncMock(side_effect=BadRequest("Message can't be deleted"))
    await commands.list_imports(_update("/list"), env.context, env.manager)
    assert panel.panel_message_id(env.context) != old

    answer = await click(env, old_data, old)
    assert "no longer active" in answer.await_args.args[0]
    assert env.bot.messages[old]['markup'] is None
    assert_single_keyboard(env)


@pytest.mark.asyncio
async def test_unstamped_legacy_buttons_are_rejected(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    answer = await click(env, "delete_final_0", panel.panel_message_id(env.context))
    assert "no longer active" in answer.await_args.args[0]
    assert env.manager.calls == []


@pytest.mark.asyncio
async def test_unauthorized_callback(env, monkeypatch):
    monkeypatch.setattr(commands, "TELEGRAM_CHAT_ID", "999")
    await commands.list_imports(_update("/list"), env.context, env.manager)  # denied too
    answer = await click(env, "x|delete_final_0", 1)
    assert answer.await_args.kwargs.get('show_alert') is True
    assert env.manager.calls == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Specific flows
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_back_to_candidates_from_preview(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    await press(env, "import_0")
    await press(env, "start_import_0")
    await press(env, f"match_0_{MB_ID[:8]}")
    await press(env, "cancel_preview")
    assert "Candidates" in live(env)['text']
    assert flows.pending_import(env.context) is None
    assert_single_keyboard(env)


@pytest.mark.asyncio
async def test_failed_import_returns_to_status_with_error(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    await press(env, "import_0")
    await press(env, "start_import_0")
    await press(env, "as_is")
    assert "as\\-is" in live(env)['text']
    await press(env, "as_is_confirm")
    assert "beet exploded" in live(env)['text']           # error shown on top of the status
    assert env.manager.current_import is not None          # nothing lost, user can retry
    assert any(cb.endswith("|retry") for cb in env.bot.callbacks(panel.panel_message_id(env.context)))
    assert env.bot.parse_errors == []


@pytest.mark.asyncio
async def test_manual_id_prompt(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    await press(env, "import_0")
    await press(env, "start_import_0")
    await press(env, "mb_id")
    assert env.context.user_data['waiting_for'] == 'mb_id'
    # buttons of the status screen are gone while the bot waits for the ID
    assert [panel.parse_callback(c)[1] for c in env.bot.callbacks(panel.panel_message_id(env.context))] == ['prompt_cancel']

    await messages.handle_message(_update("not an id"), env.context, env.manager)
    assert "Not a valid MusicBrainz" in live(env)['text']
    assert env.context.user_data['waiting_for'] == 'mb_id'

    await messages.handle_message(_update(f"https://musicbrainz.org/release/{MB_ID}"), env.context, env.manager)
    assert ('import_with_id', MB_ID, False) in env.manager.calls
    assert "Import preview" in live(env)['text']
    assert 'waiting_for' not in env.context.user_data
    assert_single_keyboard(env)


@pytest.mark.asyncio
async def test_text_without_prompt_is_ignored(env):
    await messages.handle_message(_update("hello"), env.context, env.manager)
    assert env.bot.messages == {}


@pytest.mark.asyncio
async def test_delete_keeps_unrelated_import(env):
    env.manager.current_import = {'status': 'has_candidates', 'path': str(env.albums[1]), 'candidates': []}
    await flows.show_list(env.context, CHAT, env.manager)
    await press(env, "import_0")
    await press(env, "confirm_delete_0")
    await press(env, "delete_final_0")
    assert ('delete_directory', str(env.albums[0])) in env.manager.calls
    assert env.manager.current_import is not None          # different directory: state kept
    assert "Deleted" in live(env)['text']


@pytest.mark.asyncio
async def test_images_skip_symlinks_and_are_cleaned_on_back(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    await press(env, "import_0")
    await press(env, "images_0")
    photos = [mid for mid, m in env.bot.messages.items() if m['kind'] == 'photo']
    assert [env.bot.messages[m]['text'] for m in photos] == ['cover.jpg']  # back.jpg -> secret skipped
    assert_single_keyboard(env)

    await press(env, "back_0")
    assert not any(m['kind'] == 'photo' for m in env.bot.messages.values())
    assert "Presto" in live(env)['text']
    assert len(env.bot.messages) == 1


@pytest.mark.asyncio
async def test_cancel_command_expires_panel(env):
    await commands.list_imports(_update("/list"), env.context, env.manager)
    await press(env, "import_0")
    await press(env, "start_import_0")
    pid = panel.panel_message_id(env.context)
    update = _update("/cancel")
    await commands.cancel_import(update, env.context, env.manager)
    assert env.manager.current_import is None
    assert env.bot.messages[pid]['markup'] is None
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_during_import_offers_way_back(env):
    env.manager.current_import = env.manager.start_import(str(env.albums[0]))
    await commands.list_imports(_update("/list"), env.context, env.manager)
    assert "Import in progress" in live(env)['text']
    await press(env, "show_status")
    assert "Candidates" in live(env)['text']
    assert env.bot.parse_errors == []


@pytest.mark.parametrize("kind, text, expected", [
    ("mb_id", MB_ID, MB_ID),
    ("mb_id", f"https://musicbrainz.org/release/{MB_ID.upper()}", MB_ID.upper()),
    ("mb_id", "123456", None),
    ("discogs_id", "123456", "123456"),
    ("discogs_id", "r123456", "123456"),
    ("discogs_id", "[r123456]", "123456"),
    ("discogs_id", "https://www.discogs.com/release/123456-Rush-Presto", "123456"),
    ("discogs_id", "https://www.discogs.com/Rush-Presto/release/123456", "123456"),
    ("discogs_id", "m123456", None),
    ("discogs_id", "hello", None),
])
def test_normalize_release_id(kind, text, expected):
    assert flows.normalize_release_id(kind, text) == expected
