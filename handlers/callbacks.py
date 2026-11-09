"""
Inline button dispatcher.

Every keyboard lives on the single panel message (ui/panel.py): a click is
handled only if it comes from the current panel render, otherwise it is
answered as "expired". The actual screens/flows live in handlers/flows.py.
"""
from telegram import Update
from telegram.ext import ContextTypes

from config import setup_logging
from i18n.translations import t
from ui import panel
from handlers import flows
from handlers.commands import check_allowed_user

logger = setup_logging()


class Ack:
    """Answers a callback query at most once (Telegram rejects a second answer)."""

    def __init__(self, query):
        self.query = query
        self.done = False

    async def __call__(self, text=None, alert=False):
        if self.done:
            return
        self.done = True
        try:
            await self.query.answer(text, show_alert=alert)
        except Exception as e:  # query too old, network...
            logger.debug(f"Could not answer callback query: {e}")


def _ints(text: str) -> list:
    return [int(part) for part in text.split('_') if part.isdigit()]


# Actions that need an active import
_IMPORT_ACTIONS = {
    'show_status', 'single_match_accept', 'confirm_import', 'cancel_preview',
    'mb_id', 'discogs_id', 'as_is', 'as_is_confirm', 'prompt_cancel',
    'skip', 'retry', 'cancel_import',
}


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    query = update.callback_query
    if not check_allowed_user(update, context):
        user = update.effective_user
        logger.warning(f"Unauthorized callback from user {user.id if user else '?'}: {query.data!r}")
        await query.answer(t('status.access_denied'), show_alert=True)
        return

    ack = Ack(query)
    try:
        message = query.message
        if message is None:
            await ack(t('toasts.expired'))
            return

        if not panel.is_current(context, query):
            logger.debug(f"Expired callback {query.data!r} on message {message.message_id}")
            await ack(t('toasts.expired'))
            # Old message: drop its keyboard. Never touch the live panel itself
            # (a queued double click carries the previous token of the same message).
            if message.message_id != panel.panel_message_id(context):
                await panel.strip_keyboard(context.bot, message.chat.id, message.message_id)
            return

        _, action = panel.parse_callback(query.data)
        logger.debug(f"Callback action: {action}")
        await _dispatch(action, context, message.chat.id, manager, ack)
    finally:
        await ack()


async def _dispatch(action: str, context, chat_id: int, manager, ack) -> None:
    # ── Import actions ────────────────────────────────────────────────
    if action in _IMPORT_ACTIONS or action.startswith('match_'):
        if not manager.current_import:
            await ack(t('toasts.no_import'))
            await flows.show_status(context, chat_id, manager)
            return
        await _dispatch_import(action, context, chat_id, manager, ack)
        return

    # ── Browse actions ────────────────────────────────────────────────
    if action == 'open_list':
        # From a final outcome: keep it in the chat as history, list below
        await flows.show_list(context, chat_id, manager, mode=panel.KEEP)
    elif action == 'refresh_list':
        await ack(t('list.refreshed'))
        await flows.show_list(context, chat_id, manager)
    elif action == 'cancel_file_list':
        await ack(t('list.dismissed'))
        await panel.clear_aux(context, chat_id)
        await panel.remove(context, chat_id)
    elif action.startswith('download_meta_'):
        idx, file_idx = (_ints(action) + [None, None])[:2]
        await flows.send_metadata_file(context, chat_id, manager, idx, file_idx, ack)
    elif action.startswith('confirm_delete_'):
        await flows.show_delete_confirm(context, chat_id, manager, *_ints(action)[:1] or [None])
    elif action.startswith('delete_final_'):
        await flows.delete_directory(context, chat_id, manager, *_ints(action)[:1] or [None], ack=ack)
    elif action.startswith('start_import_'):
        if manager.current_import:
            await flows.show_import_blocked(context, chat_id, manager, mode=panel.EDIT)
        else:
            await flows.start_import(context, chat_id, manager, *_ints(action)[:1] or [None], ack=ack)
    elif action.startswith('metadata_'):
        await flows.show_metadata(context, chat_id, manager, *_ints(action)[:1] or [None], ack=ack)
    elif action.startswith('images_'):
        await flows.show_images(context, chat_id, manager, *_ints(action)[:1] or [None], ack=ack)
    elif action.startswith('files_'):
        await flows.show_files(context, chat_id, manager, *_ints(action)[:1] or [None])
    elif action.startswith('import_') or action.startswith('back_'):
        # Directory details (from the list, or back from files/images/metadata)
        await flows.show_details(context, chat_id, manager, *_ints(action)[:1] or [None])
    else:
        logger.warning(f"Unknown callback action: {action!r}")


async def _dispatch_import(action: str, context, chat_id: int, manager, ack) -> None:
    current = manager.current_import

    if action == 'show_status':
        await flows.show_status(context, chat_id, manager)

    elif action == 'single_match_accept':
        match = current.get('single_match') or {}
        release_id = match.get('mb_id') or match.get('discogs_id')
        if not release_id:
            await ack(t('toasts.no_id_for_match'), True)
            return
        await flows.import_with_id(context, chat_id, manager, release_id, ack=ack)

    elif action.startswith('match_'):
        await _select_candidate(action, context, chat_id, manager, ack)

    elif action == 'confirm_import':
        pending = flows.pending_import(context)
        if not pending:
            await ack(t('toasts.preview_expired'), True)
            flows.clear_pending(context)
            await flows.show_status(context, chat_id, manager)
            return
        await flows.import_with_id(context, chat_id, manager, pending[0], ack=ack)

    elif action == 'cancel_preview':
        flows.clear_pending(context)
        if current.get('selected_index') is not None:
            current['selected_index'] = None
            manager.save_state()
        await flows.show_status(context, chat_id, manager)

    elif action in ('mb_id', 'discogs_id'):
        await flows.ask_release_id(context, chat_id, manager, action)

    elif action == 'as_is':
        await flows.ask_as_is(context, chat_id, manager)

    elif action == 'as_is_confirm':
        await flows.import_as_is(context, chat_id, manager, ack=ack)

    elif action == 'prompt_cancel':
        context.user_data.pop('waiting_for', None)
        await flows.show_status(context, chat_id, manager)

    elif action == 'skip':
        await flows.skip_import(context, chat_id, manager, ack=ack)

    elif action == 'retry':
        await flows.retry_import(context, chat_id, manager, ack=ack)

    elif action == 'cancel_import':
        await flows.cancel_import(context, chat_id, manager)


async def _select_candidate(action: str, context, chat_id: int, manager, ack) -> None:
    """match_<index>_<first 8 chars of the release id | none>"""
    current = manager.current_import
    parts = action.split('_')
    idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    candidates = current.get('candidates') or []

    if idx is None or idx >= len(candidates):
        await ack(t('toasts.candidates_changed'), True)
        await flows.show_status(context, chat_id, manager)
        return

    candidate = candidates[idx]
    expected = (candidate.get('mb_id') or candidate.get('discogs_id') or '')[:8]
    given = parts[2] if len(parts) > 2 else 'none'
    if given != 'none' and expected and not expected.startswith(given):
        logger.warning(f"Candidate id mismatch: callback {given}, expected {expected}")
        await ack(t('toasts.candidates_changed'), True)
        await flows.show_status(context, chat_id, manager)
        return

    current['selected_index'] = idx
    manager.save_state()

    if candidate.get('mb_id'):
        await flows.run_preview(context, chat_id, manager, candidate['mb_id'], 'mb', ack=ack)
    elif candidate.get('discogs_id'):
        await flows.run_preview(context, chat_id, manager, candidate['discogs_id'], 'discogs', ack=ack)
    else:
        await ack(t('toasts.no_id_for_match'), True)
