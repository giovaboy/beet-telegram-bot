"""
Single interactive "panel" message per chat.

Every screen of the bot (directory list, details, import status, preview,
prompts, progress...) is rendered into ONE message that is edited in place.
Only that message carries an active keyboard.

Each render stamps the keyboard's callback_data with a fresh random token
("a1b2c3|import_3"). A callback is accepted only if it comes from the current
panel message AND carries the current token, so buttons left on old messages,
double clicks and clicks queued during a long operation are all rejected as
"expired" instead of acting on a state they were not meant for.

State lives in context.user_data['panel'] (persisted across restarts when the
application uses PicklePersistence).
"""
import re
import secrets
from typing import Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

from config import setup_logging
from core.telegram_utils import SAFE_MESSAGE_LENGTH

logger = setup_logging()

PANEL_KEY = 'panel'
AUX_KEY = 'panel_aux'
TOKEN_SEP = '|'
MAX_CALLBACK_BYTES = 64

# Modes for show()
EDIT = 'edit'        # edit the current panel in place (falls back to BOTTOM)
BOTTOM = 'bottom'    # new panel at the bottom of the chat, old panel deleted
KEEP = 'keep'        # new panel at the bottom, old one kept as history (no keyboard)

_TRUNCATION_SUFFIX = "\n\n_…_"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Callback tokens
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def stamp(markup: Optional[InlineKeyboardMarkup], token: str) -> Optional[InlineKeyboardMarkup]:
    """Return a copy of `markup` whose callback buttons carry `token`."""
    if markup is None:
        return None
    rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for button in row:
            if button.callback_data is None:
                new_row.append(button)  # URL buttons etc.
                continue
            data = f"{token}{TOKEN_SEP}{button.callback_data}"
            if len(data.encode()) > MAX_CALLBACK_BYTES:
                logger.error(f"callback_data too long, button dropped: {data!r}")
                continue
            new_row.append(InlineKeyboardButton(button.text, callback_data=data))
        if new_row:
            rows.append(new_row)
    return InlineKeyboardMarkup(rows)


def parse_callback(data: Optional[str]) -> Tuple[Optional[str], str]:
    """Split stamped callback data into (token, action)."""
    data = data or ''
    if TOKEN_SEP in data:
        token, action = data.split(TOKEN_SEP, 1)
        return token, action
    return None, data


def is_current(context, query) -> bool:
    """True if the callback comes from the live panel with the live token."""
    state = context.user_data.get(PANEL_KEY) or {}
    token, _ = parse_callback(query.data)
    message = query.message
    return (
        token is not None
        and token == state.get('token')
        and message is not None
        and message.message_id == state.get('message_id')
    )


def panel_message_id(context) -> Optional[int]:
    return (context.user_data.get(PANEL_KEY) or {}).get('message_id')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Text safety
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fit(text: str, limit: int = SAFE_MESSAGE_LENGTH) -> str:
    """Truncate to fit one Telegram message, preferably at a line break."""
    if len(text) <= limit:
        return text
    cut = text[:limit - len(_TRUNCATION_SUFFIX)]
    newline = cut.rfind('\n')
    if newline > len(cut) * 0.6:
        cut = cut[:newline]
    # never end on a dangling escape character
    if cut.endswith('\\') and not cut.endswith('\\\\'):
        cut = cut[:-1]
    return cut + _TRUNCATION_SUFFIX


def to_plain(text: str) -> str:
    """Degrade MarkdownV2 to readable plain text (used if Telegram rejects the markup)."""
    return re.sub(r'\\(.)', r'\1', text)


def _is_parse_error(error: BadRequest) -> bool:
    return "can't parse entities" in str(error).lower()


def _is_not_modified(error: BadRequest) -> bool:
    return "message is not modified" in str(error).lower()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Low level send/edit with graceful fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _send(bot, chat_id, text, parse_mode, markup):
    try:
        return await bot.send_message(
            chat_id=chat_id, text=text, parse_mode=parse_mode,
            reply_markup=markup, disable_web_page_preview=True,
        )
    except BadRequest as e:
        if parse_mode and _is_parse_error(e):
            logger.error(f"Markup rejected by Telegram ({e}), sending plain text. Text was:\n{text}")
            return await bot.send_message(
                chat_id=chat_id, text=to_plain(text), reply_markup=markup,
                disable_web_page_preview=True,
            )
        raise


async def _edit(bot, chat_id, message_id, text, parse_mode, markup):
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, parse_mode=parse_mode,
            reply_markup=markup, disable_web_page_preview=True,
        )
    except BadRequest as e:
        if _is_not_modified(e):
            # Same text: still make sure the (re-stamped) keyboard is applied
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=message_id, reply_markup=markup)
            except BadRequest as e2:
                if not _is_not_modified(e2):
                    raise
            return
        if parse_mode and _is_parse_error(e):
            logger.error(f"Markup rejected by Telegram ({e}), editing as plain text. Text was:\n{text}")
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=to_plain(text),
                reply_markup=markup, disable_web_page_preview=True,
            )
            return
        raise


async def _delete(bot, chat_id, message_id) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as e:  # too old (>48h), already gone...
        logger.debug(f"Could not delete message {message_id}: {e}")
        return False


async def strip_keyboard(bot, chat_id, message_id) -> None:
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception as e:
        logger.debug(f"Could not strip keyboard from {message_id}: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Public API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def show(context, chat_id: int, text: str,
               markup: Optional[InlineKeyboardMarkup] = None, *,
               mode: str = EDIT, parse_mode: Optional[str] = ParseMode.MARKDOWN_V2) -> int:
    """
    Render a screen into the panel. Returns the panel message id.

    The keyboard (if any) is stamped with a fresh token, so every button of
    any previous render becomes invalid.
    """
    state = context.user_data.setdefault(PANEL_KEY, {})
    bot = context.bot
    text = fit(text)
    token = secrets.token_hex(3)
    stamped = stamp(markup, token)
    old_id = state.get('message_id') if state.get('chat_id') == chat_id else None

    if mode == EDIT and old_id:
        try:
            await _edit(bot, chat_id, old_id, text, parse_mode, stamped)
            state.update(chat_id=chat_id, message_id=old_id, token=token)
            return old_id
        except BadRequest as e:
            # Deleted by the user, too old to edit, ... → new panel at the bottom
            logger.debug(f"Panel {old_id} not editable ({e}), sending a new one")

    sent = await _send(bot, chat_id, text, parse_mode, stamped)
    if old_id and old_id != sent.message_id:
        if mode == KEEP:
            await strip_keyboard(bot, chat_id, old_id)
        elif not await _delete(bot, chat_id, old_id):
            await strip_keyboard(bot, chat_id, old_id)
    state.update(chat_id=chat_id, message_id=sent.message_id, token=token)
    return sent.message_id


async def close(context, chat_id: int, text: Optional[str] = None, *,
                parse_mode: Optional[str] = ParseMode.MARKDOWN_V2) -> None:
    """
    End the current panel: it stays in the chat as history, without keyboard.
    If `text` is given the panel is rewritten with it (e.g. the final outcome).
    """
    state = context.user_data.get(PANEL_KEY) or {}
    message_id = state.get('message_id')
    if message_id and state.get('chat_id') == chat_id:
        if text is not None:
            try:
                await _edit(context.bot, chat_id, message_id, fit(text), parse_mode, None)
            except BadRequest as e:
                logger.debug(f"Could not finalize panel {message_id} ({e}), sending outcome")
                await strip_keyboard(context.bot, chat_id, message_id)
                await _send(context.bot, chat_id, fit(text), parse_mode, None)
        else:
            await strip_keyboard(context.bot, chat_id, message_id)
    elif text is not None:
        await _send(context.bot, chat_id, fit(text), parse_mode, None)
    context.user_data.pop(PANEL_KEY, None)


async def remove(context, chat_id: int) -> None:
    """Delete the current panel message altogether."""
    state = context.user_data.pop(PANEL_KEY, None) or {}
    message_id = state.get('message_id')
    if message_id and state.get('chat_id') == chat_id:
        if not await _delete(context.bot, chat_id, message_id):
            await strip_keyboard(context.bot, chat_id, message_id)


async def expire(context, chat_id: int) -> None:
    """Invalidate the current panel's buttons (keyboard removed, message kept)."""
    await close(context, chat_id, None)


# ── Auxiliary messages (photos, documents shown next to the panel) ────

def add_aux(context, message_id: int) -> None:
    context.user_data.setdefault(AUX_KEY, []).append(message_id)


async def clear_aux(context, chat_id: int) -> None:
    for message_id in context.user_data.pop(AUX_KEY, []) or []:
        await _delete(context.bot, chat_id, message_id)
