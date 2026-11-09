"""
Handler for text messages: release IDs typed after [MusicBrainz ID] / [Discogs ID].
"""
from telegram import Update
from telegram.ext import ContextTypes

from handlers import flows
from handlers.commands import check_allowed_user


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Route free text to the pending prompt, if any; otherwise ignore it."""
    if not check_allowed_user(update, context):
        return

    waiting_for = context.user_data.get('waiting_for')
    if waiting_for not in ('mb_id', 'discogs_id'):
        return
    if not manager.current_import:
        context.user_data.pop('waiting_for', None)
        return

    await flows.handle_release_id_input(
        context, update.effective_chat.id, manager, waiting_for, update.message.text)
