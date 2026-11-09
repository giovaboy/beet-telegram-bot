"""
Handler for the bot's commands
"""
import subprocess
import os
import tempfile
from pathlib import Path
from telegram import Update
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes
from i18n.translations import t
from config import TELEGRAM_CHAT_ID, CUSTOM_COMMANDS, BEET_CONTAINER, BEET_USER, setup_logging
from core.telegram_utils import safe_reply
from handlers import flows
from ui import panel

logger = setup_logging()

# --- SECURITY FILTER FUNCTION ---
def check_allowed_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Verify if the user sending the command is the authorized user."""
    if not TELEGRAM_CHAT_ID:
        logger.warning(f"TELEGRAM_CHAT_ID not set")
        return False

    try:
        # Handle both Message and CallbackQuery
        if hasattr(update, 'effective_chat') and update.effective_chat:
            chat_id = update.effective_chat.id
        elif hasattr(update, 'callback_query') and update.callback_query:
            chat_id = update.callback_query.message.chat_id
        else:
            logger.error("Cannot determine chat_id from update")
            return False

        return str(chat_id) == str(TELEGRAM_CHAT_ID)
    except Exception as e:
        logger.error(f"Error checking user: {e}")
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Handler for /start"""
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    await update.message.reply_text(
        t('commands.start'),
        parse_mode='MarkdownV2'
    )


async def list_imports(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """
    /list: show the directory list as a fresh panel at the bottom of the chat.
    While an import is active, show the way back to it instead.
    """
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    chat_id = update.effective_chat.id
    if manager.current_import:
        logger.info("Import in progress - /list shows the active import instead")
        await flows.show_import_blocked(context, chat_id, manager, mode=panel.BOTTOM)
        return

    await flows.show_list(context, chat_id, manager, mode=panel.BOTTOM)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """/status: bring the current import panel to the bottom of the chat"""
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    if manager.current_import:
        flows.clear_pending(context)
        await flows.show_status(context, update.effective_chat.id, manager, mode=panel.BOTTOM)
    else:
        await update.message.reply_text(t('commands.no_import'), parse_mode='MarkdownV2')


async def cancel_import(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """/cancel: forget the current import; its panel stays as history without buttons"""
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    if not manager.current_import:
        await update.message.reply_text(t('commands.no_import_to_cancel'))
        return

    name = escape_markdown(Path(manager.current_import.get('path', '?')).name, version=2)
    manager.clear_state()
    flows.clear_pending(context)
    await panel.expire(context, update.effective_chat.id)
    await update.message.reply_text(t('status.cancelled_named', name=name), parse_mode='MarkdownV2')



async def execute_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Executes a predefined custom command and sends the output to the user.
    NOW WITH SAFE MESSAGE SENDING
    """
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    command_name = update.message.text.split()[0].replace('/', '')

    # Find command
    beet_command_str = None
    for item in CUSTOM_COMMANDS:
        if item['cmd'] == command_name:
            beet_command_str = item['action']
            break

    if not beet_command_str:
        await update.message.reply_text(
            t('commands.custom_not_found', command_name=command_name)
        )
        return

    # Build command
    if BEET_CONTAINER:
        action = ['docker', 'exec']
        if BEET_USER:
            action.extend(['-u', BEET_USER])
        action.append(BEET_CONTAINER)
        action.extend(beet_command_str.split())
        display_action = beet_command_str
    else:
        action = beet_command_str.split()
        display_action = beet_command_str

    display_action_escaped = escape_markdown(display_action, version=2)

    executing_msg = await update.message.reply_text(
        t('commands.executing_custom', display_action=display_action_escaped),
        parse_mode='MarkdownV2'
    )

    timeout = 600
    try:
        result = subprocess.run(
            action,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # Build output
        output = t('commands.executed_header', display_action=display_action_escaped)

        if result.stderr:
            stderr_preview = result.stderr[:500]
            stderr_escaped = escape_markdown(stderr_preview, version=2)
            if len(result.stderr) > 500:
                stderr_escaped += escape_markdown(t('commands.truncated'), version=2)
            output += t('commands.error_stderr', stderr_preview=stderr_escaped)

        if result.stdout:
            msg_body = f"```\n{result.stdout}\n```"
            msg_body_escaped = escape_markdown(msg_body, version=2)
        else:
            msg_body = t('commands.empty_output')

        final_message = output + msg_body

        # 🎯 CHECK LENGTH
        MAX_LENGTH = 4000

        if len(final_message) > MAX_LENGTH:
            # Too long - send as file
            await update.message.reply_text(
                t('commands.output_too_long', display_action=display_action_escaped),
                parse_mode='MarkdownV2'
            )

            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.txt',
                delete=False,
                encoding='utf-8'
            ) as f:
                temp_filename = f.name
                f.write(f"Command: {display_action}\n")
                f.write("=" * 50 + "\n\n")
                if result.stderr:
                    f.write("STDERR:\n")
                    f.write(result.stderr)
                    f.write("\n" + "=" * 50 + "\n\n")
                f.write("STDOUT:\n")
                f.write(result.stdout)

            with open(temp_filename, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"{command_name}_output.txt",
                    caption=t('commands.file_caption', display_action=display_action)
                )

            try:
                os.remove(temp_filename)
            except Exception as e:
                logger.error(f"Could not delete temporary output file: {e}")

        else:
            # 🎯 SAFE SEND: Use safe_reply for potentially long messages
            await safe_reply(update.message, final_message, parse_mode='MarkdownV2')

        # Delete "Executing..." message
        try:
            await executing_msg.delete()
            logger.debug(f"Command execution message {executing_msg.message_id} deleted")
        except Exception as e:
            logger.warning(f"Could not delete execution message: {e}")

    except subprocess.TimeoutExpired:
        await update.message.reply_text(
            t('commands.exec_timeout', display_action=display_action, seconds=timeout),
            parse_mode='MarkdownV2'
        )
        try:
            await executing_msg.delete()
        except:
            pass

    except Exception as e:
        logger.error(f"Error executing custom command {display_action}: {e}")
        e_escaped = escape_markdown(str(e), version=2)
        await update.message.reply_text(
            t('commands.exec_unexpected_error', error=e_escaped),
            parse_mode='MarkdownV2'
        )
        try:
            await executing_msg.delete()
        except:
            pass
