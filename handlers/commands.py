"""
Handler for the bot's commands
"""
import subprocess
import os
import tempfile
import asyncio
from pathlib import Path
from telegram import Update
from telegram.error import BadRequest
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes
from i18n.translations import t
from config import IMPORT_PATH, TELEGRAM_CHAT_ID, CUSTOM_COMMANDS, BEET_CONTAINER, BEET_USER, setup_logging
from ui.keyboards import create_directory_list_keyboard, create_import_status_keyboard
from ui.messages import format_import_status
from core.telegram_utils import safe_reply, is_message_too_long, truncate_message
from core.utils import safe_delete_message

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

def remember_dir_list(context: ContextTypes.DEFAULT_TYPE, dirs) -> None:
    """
    Save the directory list exactly as shown to the user, so that button
    indexes (import_N, delete_final_N, ...) always refer to what the user saw.
    """
    context.user_data['dir_list'] = [str(d) for d in dirs]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Handler for /start"""
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    await update.message.reply_text(
        t('commands.start'), # Message from start command
        parse_mode='MarkdownV2'
    )


async def list_imports(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """
    Shows the list of directories to import.
    
    Mode Detection:
    - If import is active → BLOCK with helpful message
    - If no import → ALLOW browsing
    
    UX Strategy:
    - ALWAYS create NEW message at bottom of chat
    - DELETE old list message if exists
    - Result: List is always the last message (fresh and clean)
    """
    logger.info("list_imports called")

    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    logger.info("User check passed")
    
    # 🎯 CHECK: Is there an active import?
    if manager.current_import:
        logger.info("Import in progress - blocking /list command")
        
        # Show helpful message with escape routes
        path = manager.current_import.get('path', 'Unknown')
        path_name = Path(path).name if path != 'Unknown' else 'Unknown'
        path_escaped = escape_markdown(path_name, version=2)
        
        blocked_msg = (
            f"⚠️ *{t('commands.import_in_progress')}*\n\n"
            f"📁 {path_escaped}\n\n"
            f"{t('commands.import_active_help')}\n\n"
            f"• `/status` \\- {t('commands.view_current_import')}\n"
            f"• `/cancel` \\- {t('commands.cancel_current_import')}"
        )
        
        await update.message.reply_text(
            blocked_msg,
            parse_mode='MarkdownV2'
        )
        return

    # No import active - proceed normally
    logger.info("No import active - showing directory list")

    dirs = manager.get_import_directories()
    logger.info(f"Found {len(dirs)} directories")
    remember_dir_list(context, dirs)

    chat_id = update.effective_chat.id
    
    # 🗑️ Step 1: DELETE old list message if exists
    old_msg_id = context.user_data.get('list_message_id')
    if old_msg_id:
        await safe_delete_message(context.bot, chat_id, old_msg_id)
        logger.debug(f"Deleted old list message {old_msg_id}")
        context.user_data.pop('list_message_id', None)
    
    # 📝 Step 2: Prepare content
    if not dirs:
        path_escaped = escape_markdown(IMPORT_PATH, version=2)
        text = t('commands.list_empty', path=path_escaped)
        keyboard = None
    else:
        text = t('commands.list_header', count=len(dirs))
        keyboard = create_directory_list_keyboard(dirs)
    
    # 📩 Step 3: ALWAYS create NEW message (at bottom of chat)
    sent = await update.message.reply_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=keyboard
    )
    
    # 💾 Step 4: Save new message ID
    context.user_data['list_message_id'] = sent.message_id
    logger.debug(f"✅ Created fresh list message: {sent.message_id}")


async def list_imports4(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """
    Shows the list of directories to import.
    
    UX Strategy:
    - ALWAYS create NEW message at bottom of chat
    - DELETE old list message if exists
    - Result: List is always the last message (fresh and clean)
    """
    logger.info("list_imports called")

    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    logger.info("User check passed")

    dirs = manager.get_import_directories()
    logger.info(f"Found {len(dirs)} directories")

    chat_id = update.effective_chat.id
    
    # 🗑️ Step 1: DELETE old list message if exists
    old_msg_id = context.user_data.get('list_message_id')
    if old_msg_id:
        await safe_delete_message(context.bot, chat_id, old_msg_id)
        logger.debug(f"Deleted old list message {old_msg_id}")
        context.user_data.pop('list_message_id', None)
    
    # 📝 Step 2: Prepare content
    if not dirs:
        path_escaped = escape_markdown(IMPORT_PATH, version=2)
        text = t('commands.list_empty', path=path_escaped)
        keyboard = None
    else:
        text = t('commands.list_header', count=len(dirs))
        keyboard = create_directory_list_keyboard(dirs)
    
    # 📩 Step 3: ALWAYS create NEW message (at bottom of chat)
    sent = await update.message.reply_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=keyboard
    )
    
    # 💾 Step 4: Save new message ID
    context.user_data['list_message_id'] = sent.message_id
    logger.debug(f"✅ Created fresh list message: {sent.message_id}")



async def list_imports3(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """
    Shows the list of directories to import.

    UX Strategy:
    - First call: Create new message
    - Subsequent calls: Try to EDIT existing message
    - If edit fails: DELETE old + CREATE new
    - Result: Only ONE list message active at a time
    """
    logger.info("list_imports called")

    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    logger.info("User check passed")

    dirs = manager.get_import_directories()
    logger.info(f"Found {len(dirs)} directories")

    chat_id = update.effective_chat.id

    # 🧹 Check if we have an existing list message
    old_msg_id = context.user_data.get('list_message_id')

    # 📝 Prepare content
    if not dirs:
        path_escaped = escape_markdown(IMPORT_PATH, version=2)
        text = t('commands.list_empty', path=path_escaped)
        keyboard = None
    else:
        text = t('commands.list_header', count=len(dirs))
        keyboard = create_directory_list_keyboard(dirs)

    # 🔄 Strategy 1: Try to EDIT existing message
    if old_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_msg_id,
                text=text,
                parse_mode='MarkdownV2',
                reply_markup=keyboard
            )
            logger.debug(f"✅ Edited existing list message {old_msg_id}")
            await update.message.reply_text(
                "✅ " + t('list.refreshed'),
                reply_to_message_id=old_msg_id
            )
            return

        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                # Content unchanged, just acknowledge
                logger.debug("List unchanged, no edit needed")
                await update.message.reply_text(
                    "ℹ️ " + t('list.no_changes'),
                    reply_to_message_id=old_msg_id
                )
                return
            else:
                # Edit failed (message deleted/too old) → fallback to delete + create
                logger.debug(f"Edit failed: {e}, will delete and recreate")
                await safe_delete_message(context.bot, chat_id, old_msg_id)
                context.user_data.pop('list_message_id', None)

        except Exception as e:
            # Unexpected error → fallback
            logger.warning(f"Unexpected error editing list: {e}")
            await safe_delete_message(context.bot, chat_id, old_msg_id)
            context.user_data.pop('list_message_id', None)

    # 📩 Strategy 2: Create NEW message (first time or after cleanup)
    sent = await update.message.reply_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=keyboard
    )

    # 💾 Save new message ID
    context.user_data['list_message_id'] = sent.message_id
    logger.debug(f"✅ Created new list message: {sent.message_id}")


async def list_imports2(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Shows the list of directories to import"""
    logger.info(f"list_imports called")
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    logger.info("User check passed")
    dirs = manager.get_import_directories()
    logger.info(f"Found {len(dirs)} directories")
    remember_dir_list(context, dirs)

    if not dirs:
        path_escaped = escape_markdown(IMPORT_PATH, version=2)
        await update.message.reply_text(
            t('commands.list_empty', path=path_escaped), # Message when the import list is empty
            parse_mode='MarkdownV2'
        )
        return

    keyboard = create_directory_list_keyboard(dirs)
    message = t('commands.list_header', count=len(dirs)) # Header for the list of directories
    # 'count' è un numero, non necessita di escape.

    # Attempt to update the existing message if present
    if 'list_message_id' in context.user_data:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['list_message_id'],
                text=message,
                parse_mode='MarkdownV2',
                reply_markup=keyboard
            )
            return
        except Exception as e:
            logger.debug(f"Could not update existing message: {e}")
            pass

    # Send new message
    msg = await update.message.reply_text(
        message,
        parse_mode='MarkdownV2',
        reply_markup=keyboard
    )
    context.user_data['list_message_id'] = msg.message_id


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Shows current import status"""
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    if manager.current_import:
        # Passes context to save the status message ID
        await format_and_send_import_status(update.message, manager.current_import, manager, context)
    else:
        await update.message.reply_text(t('commands.no_import')) # Message when no import is active


async def cancel_import(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Cancels the current import"""
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    if manager.current_import:
        # Clear import state
        manager.clear_state()

        # Try to remove keyboard from last status message if it exists
        if 'last_status_message_id' in context.user_data:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=update.effective_chat.id,
                    message_id=context.user_data['last_status_message_id'],
                    reply_markup=None
                )
            except Exception as e:
                logger.debug(f"Could not remove keyboard: {e}")

        await update.message.reply_text(t('commands.import_cancelled'))
    else:
        await update.message.reply_text(t('commands.no_import_to_cancel'))


async def format_and_send_import_status(message, result, manager, context=None):
    """
    Helper to format and send import status - WITH AUTOMATIC SPLITTING
    """
    msg = format_import_status(result)
    keyboard = create_import_status_keyboard(result, context)

    # 🎯 CHECK: Is message too long?
    if is_message_too_long(msg, parse_mode='MarkdownV2'):
        logger.warning(f"Import status message is too long ({len(msg)} chars), splitting...")

        # Strategy 1: Try truncating differences
        if result.get('single_match') and result['single_match'].get('differences'):
            logger.info("Truncating differences to fit message limit")

            # Limit differences to first 10 instead of 100
            original_diffs = result['single_match']['differences']
            result['single_match']['differences'] = original_diffs[:10]

            # Regenerate message
            msg = format_import_status(result)

            if is_message_too_long(msg, parse_mode='MarkdownV2'):
                # Still too long? Truncate more
                logger.warning("Still too long after truncating differences")
                msg = truncate_message(msg, suffix="\n\n_\\.\\.\\.message truncated_")

        elif result.get('candidates'):
            logger.info("Truncating candidates to fit message limit")

            # Limit candidates to first 5 instead of 10
            result['candidates'] = result['candidates'][:5]

            # Regenerate message
            msg = format_import_status(result)

            if is_message_too_long(msg, parse_mode='MarkdownV2'):
                logger.warning("Still too long after truncating candidates")
                msg = truncate_message(msg, suffix="\n\n_\\.\\.\\.message truncated_")

        else:
            # Generic truncation
            logger.warning("Truncating message generically")
            msg = truncate_message(msg, suffix="\n\n_\\.\\.\\.message truncated_")

    # 🎯 SAFE SEND: Use safe_reply instead of direct reply_text
    sent_messages = await safe_reply(
        message,
        msg,
        parse_mode='MarkdownV2',
        reply_markup=keyboard,
        disable_web_page_preview=False
    )

    # Save first message ID for later cleanup
    if context and sent_messages:
        context.user_data['last_status_message_id'] = sent_messages[0].message_id

        # If multiple messages, save all IDs
        if len(sent_messages) > 1:
            context.user_data['status_message_ids'] = [m.message_id for m in sent_messages]
            logger.info(f"Import status split into {len(sent_messages)} messages")

    return sent_messages[0].message_id if sent_messages else None


async def cleanup_old_status_message(context, chat_id, label_key='completed_label'):
    """
    Removes keyboard and adds history label to the previous message(s)
    NOW HANDLES MULTIPLE MESSAGES
    """
    # Handle single message (backward compatibility)
    if 'last_status_message_id' in context.user_data:
        try:
            old_msg_id = context.user_data['last_status_message_id']

            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=old_msg_id,
                    reply_markup=None
                )
                logger.debug(f"Keyboard removed from message {old_msg_id}")
            except Exception as e:
                logger.debug(f"Could not modify previous message: {e}")

        except Exception as e:
            logger.debug(f"Error during previous message cleanup: {e}")

        del context.user_data['last_status_message_id']

    # Handle multiple messages (from splitting)
    if 'status_message_ids' in context.user_data:
        for msg_id in context.user_data['status_message_ids']:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=None
                )
                logger.debug(f"Keyboard removed from split message {msg_id}")
            except Exception as e:
                logger.debug(f"Could not modify split message {msg_id}: {e}")

        del context.user_data['status_message_ids']


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
