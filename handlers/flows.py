"""
Screens and flows of the bot, all rendered into the single panel message
(see ui/panel.py). Used by button callbacks, commands and text messages.

Long beet / filesystem operations run in a worker thread so the bot stays
responsive; before starting one, the panel is switched to a "⏳ ..." screen
without keyboard, so nothing can be clicked while it runs.
"""
import asyncio
import copy
import re
import time
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown

from config import IMPORT_PATH, DIFF_STYLE, setup_logging
from core.directory_analyzer import analyze_directory, get_search_query, get_relative_path
from core.parsers import format_difference_with_diff, extract_musicbrainz_id
from core.telegram_utils import is_message_too_long
from core.utils import is_within_import_path
from i18n.translations import t
from ui import panel
from ui.keyboards import (
    create_directory_list_keyboard,
    create_directory_details_keyboard,
    create_delete_confirm_keyboard,
    create_back_keyboard,
    create_import_status_keyboard,
)
from ui.messages import format_directory_details, format_file_list, format_import_status, get_file_icon

logger = setup_logging()

MAX_IMAGES = 10
MAX_METADATA_SHOWN = 15
MAX_METADATA_SIZE_MB = 50
MAX_PREVIEW_DIFFERENCES = 15
PENDING_IMPORT_TIMEOUT = 2 * 60 * 60  # 2 hours

_BROWSE_KEYS = ('current_dir_structure', 'cached_dir_path', 'metadata_files_list')
_PENDING_KEYS = ('pending_import_id', 'pending_import_source', 'pending_import_ts', 'waiting_for')


def esc(value) -> str:
    return escape_markdown(str(value), version=2)


async def _ack(ack, text: Optional[str] = None, alert: bool = False) -> None:
    """Answer the callback query (no-op when the flow was not started by a button)."""
    if ack is not None:
        await ack(text, alert)


def _short_error(message: str, limit: int = 300) -> str:
    """Last meaningful lines of a (possibly huge) beet error output."""
    message = (message or '').strip() or 'Unknown error'
    if len(message) > limit:
        message = '…' + message[-limit:]
    return message


def _error_banner(message: str) -> str:
    return t('status.error_banner', error=esc(_short_error(message)))


def _open_list_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t('buttons.open_list'), callback_data="open_list")]])


def _cancel_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t('buttons.cancel'), callback_data="prompt_cancel")]])


def clear_pending(context) -> None:
    for key in _PENDING_KEYS:
        context.user_data.pop(key, None)


def _reset_browse(context) -> None:
    for key in _BROWSE_KEYS:
        context.user_data.pop(key, None)


def _display_name(path) -> str:
    return get_relative_path(Path(path), Path(IMPORT_PATH))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Directory selection (snapshot of the list the user actually saw)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def remember_dir_list(context, dirs) -> None:
    """
    Save the directory list exactly as shown to the user, so that button
    indexes (import_N, delete_final_N, ...) always refer to what the user saw.
    """
    context.user_data['dir_list'] = [str(d) for d in dirs]


def get_selected_dir(context, idx):
    """
    Resolve a button index against the directory list the user actually saw
    (snapshot saved by remember_dir_list), NOT against a freshly re-sorted
    listing: new downloads or mtime changes would otherwise shift the indexes
    and e.g. 'delete_final_3' could hit a different directory.
    """
    snapshot = context.user_data.get('dir_list') or []
    if idx is None or idx >= len(snapshot):
        return None
    selected = Path(snapshot[idx])
    if not selected.is_dir() or not is_within_import_path(selected.parent, allow_root=True):
        return None
    return selected


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Browse screens
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_list(manager):
    """Filesystem scan + keyboard (computes folder sizes): run in a thread."""
    dirs = manager.get_import_directories()
    keyboard = create_directory_list_keyboard(dirs) if dirs else None
    return dirs, keyboard


async def show_list(context, chat_id, manager, *, mode=panel.EDIT, banner: str = '') -> None:
    await panel.clear_aux(context, chat_id)
    _reset_browse(context)
    dirs, keyboard = await asyncio.to_thread(_build_list, manager)
    remember_dir_list(context, dirs)
    if dirs:
        text = banner + t('commands.list_header', count=len(dirs))
    else:
        text = banner + t('commands.list_empty', path=esc(IMPORT_PATH))
    await panel.show(context, chat_id, text, keyboard, mode=mode)


async def show_import_blocked(context, chat_id, manager, *, mode=panel.BOTTOM) -> None:
    """Shown by /list while an import is active: offer the way back instead of a dead end."""
    name = esc(Path(manager.current_import.get('path', '?')).name)
    text = (
        f"⚠️ *{t('commands.import_in_progress')}*\n\n"
        f"📁 {name}\n\n"
        f"{t('commands.import_active_help')}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('buttons.show_status'), callback_data="show_status")],
        [InlineKeyboardButton(t('buttons.cancel_import'), callback_data="cancel_import")],
    ])
    await panel.show(context, chat_id, text, keyboard, mode=mode)


async def _structure_for(context, chat_id, selected: Path) -> dict:
    structure = context.user_data.get('current_dir_structure')
    if structure and context.user_data.get('cached_dir_path') == str(selected):
        return structure
    await panel.show(context, chat_id, t('directory.analyzing', name=esc(selected.name)))
    structure = await asyncio.to_thread(analyze_directory, str(selected))
    context.user_data['current_dir_structure'] = structure
    context.user_data['cached_dir_path'] = str(selected)
    return structure


def _details_text(selected: Path, structure: dict) -> str:
    parts = _display_name(selected).split(' / ')
    parent = ' / '.join(parts[:-1]) if len(parts) > 1 else None
    return format_directory_details(selected.name, structure, parent)


async def show_details(context, chat_id, manager, idx, *, mode=panel.EDIT) -> None:
    selected = get_selected_dir(context, idx)
    if not selected:
        await show_list(context, chat_id, manager, banner=t('directory.not_available') + "\n\n")
        return
    await panel.clear_aux(context, chat_id)
    structure = await _structure_for(context, chat_id, selected)
    keyboard = create_directory_details_keyboard(idx, structure, get_search_query(str(selected)))
    await panel.show(context, chat_id, _details_text(selected, structure), keyboard, mode=mode)


async def show_files(context, chat_id, manager, idx) -> None:
    selected = get_selected_dir(context, idx)
    if not selected:
        await show_list(context, chat_id, manager, banner=t('directory.not_available') + "\n\n")
        return
    structure = await _structure_for(context, chat_id, selected)
    await panel.show(context, chat_id, format_file_list(structure), create_back_keyboard(idx))


def _collect(structure: dict, key: str) -> list:
    items = [dict(item) for item in structure.get(key, [])]
    for disc in structure.get('discs', []):
        for item in disc.get(key, []):
            items.append({**item, 'disc': disc['name']})
    return items


async def show_images(context, chat_id, manager, idx, ack=None) -> None:
    selected = get_selected_dir(context, idx)
    if not selected:
        await show_list(context, chat_id, manager, banner=t('directory.not_available') + "\n\n")
        return
    structure = await _structure_for(context, chat_id, selected)
    images = _collect(structure, 'images')
    if not images:
        await _ack(ack, t('toasts.no_images'))
        return

    await _ack(ack)
    await panel.show(context, chat_id, t('directory.sending_images', count=len(images)))
    await panel.clear_aux(context, chat_id)
    for img in images[:MAX_IMAGES]:
        # A downloaded "cover.jpg" may be a symlink to any file in the container
        if not is_within_import_path(img['path']):
            logger.warning(f"Skipping image outside import path: {img['path']}")
            continue
        caption = f"{img['disc']}: {img['name']}" if 'disc' in img else img['name']
        try:
            with open(img['path'], 'rb') as f:
                sent = await context.bot.send_photo(chat_id=chat_id, photo=f, caption=caption)
            panel.add_aux(context, sent.message_id)
        except Exception as e:
            logger.error(f"Error sending image {img['name']}: {e}")

    text = t('directory.images_sent')
    if len(images) > MAX_IMAGES:
        text += "\n" + t('directory.images_limited', count=len(images))
    # New panel below the photos, so [Back] is where the user is looking
    await panel.show(context, chat_id, text, create_back_keyboard(idx), mode=panel.BOTTOM)


def _metadata_keyboard(idx, files) -> InlineKeyboardMarkup:
    rows = []
    for i, meta in enumerate(files):
        size = meta['size']
        size_str = f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KB"
        name = meta['name'] if len(meta['name']) <= 35 else meta['name'][:32] + "..."
        disc = f"[{meta['disc']}] " if 'disc' in meta else ""
        rows.append([InlineKeyboardButton(
            f"{get_file_icon(meta['ext'])} {disc}{name} ({size_str})",
            callback_data=f"download_meta_{idx}_{i}",
        )])
    rows.append([InlineKeyboardButton(t('buttons.back'), callback_data=f"back_{idx}")])
    return InlineKeyboardMarkup(rows)


async def show_metadata(context, chat_id, manager, idx, ack=None, *, mode=panel.EDIT) -> None:
    selected = get_selected_dir(context, idx)
    if not selected:
        await show_list(context, chat_id, manager, banner=t('directory.not_available') + "\n\n")
        return
    structure = await _structure_for(context, chat_id, selected)
    files = _collect(structure, 'metadata_files')
    if not files:
        await _ack(ack, t('toasts.no_metadata'))
        return
    files.sort(key=lambda m: m['size'], reverse=True)
    shown = files[:MAX_METADATA_SHOWN]
    context.user_data['metadata_files_list'] = shown

    text = f"📄 *{t('directory.metadata_header', count=len(files))}*\n"
    if len(files) > MAX_METADATA_SHOWN:
        text += f"\n_\\({t('directory.showing_first', count=MAX_METADATA_SHOWN)}\\)_\n"
    await panel.show(context, chat_id, text, _metadata_keyboard(idx, shown), mode=mode)


async def send_metadata_file(context, chat_id, manager, idx, file_idx, ack=None) -> None:
    files = context.user_data.get('metadata_files_list') or []
    if file_idx is None or file_idx >= len(files):
        await _ack(ack, t('toasts.file_unavailable'))
        return
    meta = files[file_idx]

    size_mb = meta['size'] / (1024 * 1024)
    if size_mb > MAX_METADATA_SIZE_MB:
        await _ack(ack, t('toasts.file_too_large', size=f"{size_mb:.1f}", limit=MAX_METADATA_SIZE_MB), True)
        return
    # A downloaded "info.txt" may be a symlink to any file in the container
    # (e.g. /proc/self/environ, which holds the bot token): never follow it out
    if not is_within_import_path(meta['path']):
        logger.warning(f"Refusing to send file outside import path: {meta['path']}")
        await _ack(ack, t('toasts.file_unavailable'), True)
        return

    await _ack(ack)
    await panel.show(context, chat_id, f"📤 {esc(t('metadata.sending_file'))}\\.\\.\\.")
    try:
        disc = f"[{meta['disc']}] " if 'disc' in meta else ""
        with open(meta['path'], 'rb') as f:
            sent = await context.bot.send_document(
                chat_id=chat_id, document=f, filename=meta['name'], caption=f"{disc}{meta['name']}")
        panel.add_aux(context, sent.message_id)
    except Exception as e:
        logger.error(f"Error sending metadata file {meta['path']}: {e}", exc_info=True)
    # Panel back below the document
    await show_metadata(context, chat_id, manager, idx, mode=panel.BOTTOM)


async def show_delete_confirm(context, chat_id, manager, idx) -> None:
    selected = get_selected_dir(context, idx)
    if not selected:
        await show_list(context, chat_id, manager, banner=t('directory.not_available') + "\n\n")
        return
    await panel.show(
        context, chat_id,
        t('delete.confirm', name=esc(_display_name(selected))),
        create_delete_confirm_keyboard(idx, selected.name),
    )


async def delete_directory(context, chat_id, manager, idx, ack=None) -> None:
    selected = get_selected_dir(context, idx)
    if not selected:
        await show_list(context, chat_id, manager, banner=t('directory.not_available') + "\n\n")
        return
    result = await asyncio.to_thread(manager.delete_directory, str(selected))
    if result['status'] != 'success':
        if result['message'] in ('error_root', 'error_not_found'):
            banner = t(f"delete.{result['message']}")
        else:
            banner = t('delete.error', error=esc(_short_error(result['message'])))
        await show_list(context, chat_id, manager, banner=banner + "\n\n")
        return

    # Only forget the import state if it was about this very directory
    current = manager.current_import or {}
    if current.get('path') == str(selected):
        manager.clear_state()
        clear_pending(context)
    await _ack(ack, t('toasts.deleted'))
    await show_list(context, chat_id, manager,
                    banner=t('delete.deleted_banner', name=esc(_display_name(selected))))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Import screens
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def status_text(result: dict) -> str:
    """Import status, shrunk if needed (never mutates the stored state)."""
    text = format_import_status(result)
    if not is_message_too_long(text, parse_mode='MarkdownV2'):
        return text
    shrunk = copy.deepcopy(result)
    if shrunk.get('single_match') and shrunk['single_match'].get('differences'):
        shrunk['single_match']['differences'] = shrunk['single_match']['differences'][:10]
    elif shrunk.get('candidates'):
        shrunk['candidates'] = shrunk['candidates'][:5]
    return format_import_status(shrunk)  # panel.fit() truncates whatever is left


async def show_status(context, chat_id, manager, *, mode=panel.EDIT, banner: str = '') -> None:
    result = manager.current_import
    if not result:
        await panel.show(context, chat_id, t('commands.no_import'), _open_list_keyboard(), mode=mode)
        return
    await panel.show(context, chat_id, banner + status_text(result),
                     create_import_status_keyboard(result, context), mode=mode)


async def _outcome(context, chat_id, text: str) -> None:
    """Final outcome of an import: stays in the chat, with a way to continue."""
    await panel.show(context, chat_id, text, _open_list_keyboard())


async def start_import(context, chat_id, manager, idx, ack=None) -> None:
    selected = get_selected_dir(context, idx)
    if not selected:
        await show_list(context, chat_id, manager, banner=t('directory.not_available') + "\n\n")
        return
    await _ack(ack)
    await panel.clear_aux(context, chat_id)
    _reset_browse(context)
    clear_pending(context)
    await panel.show(context, chat_id, t('import.starting', name=esc(_display_name(selected))))
    try:
        result = await asyncio.to_thread(manager.start_import, str(selected))
    except Exception as e:
        logger.error(f"Error starting import: {e}", exc_info=True)
        await show_list(context, chat_id, manager, banner=_error_banner(str(e)))
        return
    manager.current_import = result
    manager.save_state()
    await show_status(context, chat_id, manager)


async def retry_import(context, chat_id, manager, ack=None) -> None:
    path = manager.current_import['path']
    await _ack(ack)
    clear_pending(context)
    await panel.show(context, chat_id, t('import.retrying'))
    result = await asyncio.to_thread(manager.start_import, path)
    manager.current_import = result
    manager.save_state()
    await show_status(context, chat_id, manager)


async def cancel_import(context, chat_id, manager) -> None:
    name = esc(Path(manager.current_import.get('path', '?')).name)
    manager.clear_state()
    clear_pending(context)
    await _outcome(context, chat_id, t('status.cancelled_named', name=name))


async def skip_import(context, chat_id, manager, ack=None) -> None:
    path = manager.current_import['path']
    await _ack(ack)
    await panel.show(context, chat_id, t('import.skipping'))
    result = await asyncio.to_thread(manager.skip_item, path)
    if result['status'] != 'success':
        await show_status(context, chat_id, manager, banner=_error_banner(result['message']))
        return
    manager.clear_state()
    clear_pending(context)
    await _outcome(context, chat_id, t('status.skipped_named', name=esc(Path(path).name),
                                       message=esc(result['message'])))


async def _run_final_import(context, chat_id, manager, operation, *args) -> None:
    """Run a real import (not a preview); on failure go back to the status screen."""
    path = manager.current_import['path']
    name = esc(Path(path).name)
    await panel.show(context, chat_id, t('import.running', name=name))
    try:
        result = await asyncio.to_thread(operation, *args)
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        result = {'status': 'error', 'message': str(e)}

    if result.get('status') == 'success':
        manager.clear_state()
        clear_pending(context)
        await _outcome(context, chat_id, t('status.completed_named', name=name))
    else:
        # A failed import leaves the stored state untouched: the candidates are
        # still there and the user can pick another option from the same screen.
        await show_status(context, chat_id, manager, banner=_error_banner(result.get('message', '')))


async def import_with_id(context, chat_id, manager, id_value: str, ack=None) -> None:
    await _ack(ack)
    await _run_final_import(context, chat_id, manager, manager.import_with_id,
                            manager.current_import['path'], id_value, True)


async def import_as_is(context, chat_id, manager, ack=None) -> None:
    await _ack(ack)
    clear_pending(context)
    await _run_final_import(context, chat_id, manager, manager.import_as_is,
                            manager.current_import['path'])


# ── Preview of a specific release ────────────────────────────────────

def _source_name(id_type: str) -> str:
    return "MusicBrainz" if id_type == 'mb' else "Discogs"


def preview_text(result: dict, id_type: str) -> str:
    preview = result.get("preview", {}).get("single_match") or {}

    def field(key):
        value = preview.get(key)
        return esc('?' if value is None else value)

    msg = f"🎯 *{t('status.preview')}*\n\n"
    msg += f"🎤 {t('fields.artist', artist=field('artist'))}\n"
    msg += f"💿 {t('fields.album', album=field('album'))}\n"
    msg += f"📅 {t('fields.year', year=field('year'))}\n"
    msg += f"📊 {t('fields.similarity', similarity=field('similarity'))}\n"

    differences = preview.get("differences") or []
    if differences:
        msg += "\n⚠️ *" + t('status.differences') + "*\n"
        for diff in differences[:MAX_PREVIEW_DIFFERENCES]:
            msg += format_difference_with_diff(diff, style=DIFF_STYLE) + "\n"
        if len(differences) > MAX_PREVIEW_DIFFERENCES:
            msg += f"\n{t('fields.more_diff', remaining=len(differences) - MAX_PREVIEW_DIFFERENCES)}\n"

    url = preview.get("mb_url") if id_type == 'mb' else preview.get("discogs_url")
    if url:
        msg += f"\n{t('fields.link', source=_source_name(id_type), url=url)}\n"
    return msg


async def run_preview(context, chat_id, manager, id_value: str, id_type: str, *,
                      mode=panel.EDIT, ack=None) -> None:
    """Ask beet for a preview of a release; the user then confirms or goes back."""
    await _ack(ack)
    await panel.show(context, chat_id, t('import.loading_preview', source=_source_name(id_type)), mode=mode)
    try:
        result = await asyncio.to_thread(
            manager.import_with_id, manager.current_import['path'], id_value, False)
    except Exception as e:
        logger.error(f"Preview failed: {e}", exc_info=True)
        result = {'status': 'error', 'message': str(e)}

    if result.get('status') != 'needs_confirmation':
        clear_pending(context)
        await show_status(context, chat_id, manager, banner=_error_banner(result.get('message', '')))
        return

    context.user_data.update(
        pending_import_id=id_value,
        pending_import_source=id_type,
        pending_import_ts=time.time(),
    )
    back_label = (t('buttons.back_to_candidates') if manager.current_import.get('candidates')
                  else t('buttons.back'))
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('buttons.confirm_import'), callback_data="confirm_import")],
        [InlineKeyboardButton(back_label, callback_data="cancel_preview")],
    ])
    await panel.show(context, chat_id, preview_text(result, id_type), keyboard)


def pending_import(context):
    """(id, source) of the previewed release, or None if missing/expired."""
    id_value = context.user_data.get('pending_import_id')
    source = context.user_data.get('pending_import_source')
    ts = context.user_data.get('pending_import_ts') or 0
    if not id_value or not source or time.time() - ts > PENDING_IMPORT_TIMEOUT:
        return None
    return id_value, source


# ── Manual input ─────────────────────────────────────────────────────

_DISCOGS_ID_RE = re.compile(
    r'^\s*(?:\[?r?(\d+)\]?|.*discogs\.com/(?:[^/]+/)?release/(\d+).*)\s*$', re.IGNORECASE)


def normalize_release_id(kind: str, text: str) -> Optional[str]:
    """
    Accept what users typically paste and return the ID beet expects:
    - MusicBrainz: a release UUID, or any URL containing it
    - Discogs: 123456, r123456, [r123456], or a discogs.com/release/... URL
    """
    text = (text or '').strip()
    if kind == 'mb_id':
        return extract_musicbrainz_id(text)
    if kind == 'discogs_id':
        match = _DISCOGS_ID_RE.match(text)
        if match:
            return match.group(1) or match.group(2)
    return None


async def ask_release_id(context, chat_id, manager, kind: str, *, banner: str = '', mode=panel.EDIT) -> None:
    context.user_data['waiting_for'] = kind
    await panel.show(context, chat_id, banner + t(f'prompts.{kind}'), _cancel_prompt_keyboard(), mode=mode)


async def ask_as_is(context, chat_id, manager) -> None:
    name = esc(Path(manager.current_import.get('path', '?')).name)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('buttons.confirm_as_is'), callback_data="as_is_confirm")],
        [InlineKeyboardButton(t('buttons.cancel'), callback_data="prompt_cancel")],
    ])
    await panel.show(context, chat_id, t('prompts.confirm_as_is', name=name), keyboard)


async def handle_release_id_input(context, chat_id, manager, kind: str, text: str) -> None:
    """The user typed an ID after pressing [MusicBrainz ID] / [Discogs ID]."""
    id_value = normalize_release_id(kind, text)
    if not id_value:
        # Ask again below the user's message
        await ask_release_id(context, chat_id, manager, kind,
                             banner=t(f'prompts.invalid_{kind}'), mode=panel.BOTTOM)
        return
    context.user_data.pop('waiting_for', None)
    id_type = 'mb' if kind == 'mb_id' else 'discogs'
    await run_preview(context, chat_id, manager, id_value, id_type, mode=panel.BOTTOM)
