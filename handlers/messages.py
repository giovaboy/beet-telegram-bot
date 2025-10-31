"""
Handler for text messages (ID input, confirmations)
"""
import subprocess
from telegram import Update
from telegram.ext import ContextTypes
from i18n.translations import t
from config import BEET_CONTAINER, BEET_USER
from handlers.commands import cleanup_old_status_message
from handlers.callbacks import import_with_mb_id, import_with_discogs_id

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Handler for messages (entered IDs)"""
    if 'waiting_for' not in context.user_data or not manager.current_import:
        return

    text = update.message.text.strip()
    waiting_for = context.user_data['waiting_for']

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Case 1: MusicBrainz ID
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if waiting_for == 'mb_id':
        await update.message.reply_text(t('import.with_mb_id'), parse_mode='MarkdownV2')
        await import_with_mb_id(
            update=update,
            mb_id=text,
            context=context,
            manager=manager,
            auto_apply=False
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Case 2: Discogs ID
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif waiting_for == 'discogs_id':
        await update.message.reply_text(t('import.with_discogs_id'), parse_mode='MarkdownV2')
        await import_with_discogs_id(
            update=update,
            discogs_id=text,
            context=context,
            manager=manager,
            auto_apply=False
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Case 3: Confirm "as-is" import
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif waiting_for == 'confirm_as_is':
        # Check if the user confirms the "as-is" import
        if text.upper() in ['SI', 'YES', 'Y', 'OK', 'SÌ']:
            await update.message.reply_text(t('import.as_is'), parse_mode='MarkdownV2')

            try:
                beet_path = manager.translate_path_for_beet(manager.current_import['path'])
                beet_cmd = ['beet', 'import', beet_path]

                if BEET_CONTAINER:
                    cmd = ['docker', 'exec', '-i']
                    if BEET_USER:
                        cmd.extend(['-u', BEET_USER])
                    cmd.extend([BEET_CONTAINER] + beet_cmd)
                else:
                    cmd = beet_cmd

                result = subprocess.run(
                    cmd,
                    input='U\n',
                    capture_output=True,
                    text=True,
                    timeout=300
                )

                if result.returncode == 0:
                    # Cleanup old message before confirming
                    await cleanup_old_status_message(context, update.message.chat_id, 'completed_label')
                    await update.message.reply_text(t('status.import_completed'), parse_mode='MarkdownV2')
                    manager.clear_state()
                else:
                    error_output = result.stderr[:200] if result.stderr else "Unknown error"
                    await update.message.reply_text(f"❌ {error_output}")

            except subprocess.TimeoutExpired:
                await update.message.reply_text("⏱️ Import timed out (>5 minutes)")
            except Exception as e:
                await update.message.reply_text(f"❌ {str(e)}")
        else:
            await update.message.reply_text(t('prompts.cancelled'))

    # Clear the waiting state
    del context.user_data['waiting_for']