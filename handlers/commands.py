"""
Handler for the bot's commands
"""
import subprocess
import os
import tempfile
import asyncio
from telegram import Update
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes
from i18n.translations import t
from config import IMPORT_PATH, TELEGRAM_CHAT_ID, CUSTOM_COMMANDS, BEET_CONTAINER, BEET_USER, setup_logging
from ui.keyboards import create_directory_list_keyboard, create_import_status_keyboard
from ui.messages import format_import_status

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
        t('commands.start'), # Message from start command
        parse_mode='MarkdownV2'
    )


async def list_imports(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Shows the list of directories to import"""
    logger.info("list_imports called")

    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    logger.info("User check passed")

    dirs = manager.get_import_directories()
    logger.info(f"Found {len(dirs)} directories")

    chat_id = update.effective_chat.id
    text = t('commands.list_header', count=len(dirs))

    # 🧹 Pulisci tastiera precedente se presente
    old_msg_id = context.user_data.get('list_message_id')
    if old_msg_id:
        try:
            # Rimuove tastiera e testo precedente, per evitare duplicati
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=old_msg_id,
                reply_markup=None
            )
            logger.debug(f"Removed old list keyboard from message {old_msg_id}")
        except Exception as e:
            logger.debug(f"Could not remove old keyboard: {e}")
            try:
                # Come fallback, elimina il messaggio
                await safe_delete_message(context.bot, chat_id, old_msg_id)
                logger.debug(f"Deleted old list message {old_msg_id}")
            except Exception as e2:
                logger.debug(f"Could not delete old message: {e2}")
        context.user_data.pop('list_message_id', None)

    # 🪣 Se non ci sono directory, mostra messaggio vuoto e basta
    if not dirs:
        path_escaped = escape_markdown(IMPORT_PATH, version=2)
        msg = await update.message.reply_text(
            t('commands.list_empty', path=path_escaped),
            parse_mode='MarkdownV2'
        )
        return

    # 🎛️ Crea nuova tastiera
    keyboard = create_directory_list_keyboard(dirs)

    # 📩 Invia nuovo messaggio
    sent = await update.message.reply_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=keyboard
    )

    # 💾 Salva ID del messaggio corrente
    context.user_data['list_message_id'] = sent.message_id
    logger.debug(f"New list message created: {sent.message_id}")


async def list_imports2(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Shows the list of directories to import"""
    logger.info(f"list_imports called")
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    logger.info("User check passed")
    dirs = manager.get_import_directories()
    logger.info(f"Found {len(dirs)} directories")

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
    """Helper to format and send import status"""

    # --- NOTA ---
    # Assumiamo che format_import_status(result) restituisca una stringa
    # già formattata e con escape corretto per MarkdownV2.
    msg = format_import_status(result)
    keyboard = create_import_status_keyboard(result, context)

    sent_message = await message.reply_text(
        msg,
        parse_mode='MarkdownV2',
        reply_markup=keyboard,
        disable_web_page_preview=False
    )

    # Save message_id for later cleanup
    if context:
        context.user_data['last_status_message_id'] = sent_message.message_id

    return sent_message.message_id


async def cleanup_old_status_message(context, chat_id, label_key='completed_label'):
    """Removes keyboard and adds history label to the previous message"""
    #return
    if 'last_status_message_id' not in context.user_data:
        return

    try:
        old_msg_id = context.user_data['last_status_message_id']

        try:
            # Remove the keyboard from the previous message
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

    # Clear the reference
    del context.user_data['last_status_message_id']


async def execute_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes a predefined custom command and sends the output to the user."""
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return

    # The command is already formatted as /<cmd_name> (e.g., /check)
    command_name = update.message.text.split()[0].replace('/', '')

    # --- 1. SEARCH COMMAND AND PREPARE beet_command_str ---
    beet_command_str = None
    for item in CUSTOM_COMMANDS:
        if item['cmd'] == command_name:
            beet_command_str = item['action']
            break

    if not beet_command_str:
        # Nessun parse_mode, nessun escape necessario
        await update.message.reply_text(
            t('commands.custom_not_found', command_name=command_name) # Message when custom command is not found
        )
        return

    # --- 2. BUILD ACTION (Argument List) ---
    if BEET_CONTAINER:
        # Execution via Docker
        action = ['docker', 'exec']

        if BEET_USER:
            action.extend(['-u', BEET_USER])

        # Add container name and Beet command
        action.append(BEET_CONTAINER)
        action.extend(beet_command_str.split())

        display_action = beet_command_str
    else:
        # Direct execution
        action = beet_command_str.split()
        display_action = beet_command_str

    # --- 3. EXECUTION AND ERROR HANDLING ---

    # --- ESCAPE AGGIUNTO ---
    # Il comando (display_action) viene mostrato all'utente e necessita di escape
    display_action_escaped = escape_markdown(display_action, version=2)

    # Send temporary message and save the reference
    executing_msg = await update.message.reply_text(
        t('commands.executing_custom', display_action=display_action_escaped), # Message for command execution start
        parse_mode='MarkdownV2'
    )
    timeout = 300
    try:
        result = subprocess.run(
            action,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # Initialize final output with success status
        output = t('commands.executed_header', display_action=display_action_escaped) # Message for command executed (header)

        # Add stderr (Error Output, even if command exits with code 0)
        if result.stderr:
            # --- ESCAPE AGGIUNTO ---
            # stderr è output raw e va escapato
            stderr_preview = result.stderr[:500]
            stderr_escaped = escape_markdown(stderr_preview, version=2)

            if len(result.stderr) > 500:
                # Anche il testo 'truncated' deve essere escapato se contiene markdown
                stderr_escaped += escape_markdown(t('commands.truncated'), version=2) # Message for truncation

            output += t('commands.error_stderr', stderr_preview=stderr_escaped) # Message for stderr

        # Add stdout (The actual command result)
        if result.stdout:
            # L'output dentro un blocco ``` non necessita di escape
            msg_body = f"```\n{result.stdout}\n```"
            msg_body_escaped = escape_markdown(msg_body, version=2)
        else:
            msg_body = t('commands.empty_output') # Message for empty output

        # Combine header with body
        final_message = output + msg_body

        # ✅ LONG MESSAGE HANDLING
        MAX_LENGTH = 4000  # Telegram limit is 4096. Using 4000 for safety.

        if len(final_message) > MAX_LENGTH:
            # Message too long, send header and output as a text file
            await update.message.reply_text(
                t('commands.output_too_long', display_action=display_action_escaped), # Message for output too long
                parse_mode='MarkdownV2'
            )

            # Create unique temporary file name
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.txt',
                delete=False,
                encoding='utf-8'
            ) as f:
                temp_filename = f.name
                f.write(f"Command: {display_action}\n") # display_action raw nel file di testo
                f.write("=" * 50 + "\n\n")
                if result.stderr:
                    f.write("STDERR:\n")
                    f.write(result.stderr)
                    f.write("\n" + "=" * 50 + "\n\n")
                f.write("STDOUT:\n")
                f.write(result.stdout)

            # Send result as a text file
            with open(temp_filename, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"{command_name}_output.txt",
                    # Nessun parse_mode per la caption, nessun escape
                    caption=t('commands.file_caption', display_action=display_action) # Caption for the output file
                )

            # Clean up temporary file
            try:
                os.remove(temp_filename)
            except Exception as e:
                logger.error(f"Could not delete temporary output file: {e}")

        else:
            # Short message, send normally
            await update.message.reply_text(final_message, parse_mode='MarkdownV2')

        # ✅ DELETE the "Executing..." message
        try:
            await executing_msg.delete()
            logger.debug(f"Command execution message {executing_msg.message_id} deleted")
        except Exception as e:
            logger.warning(f"Could not delete execution message: {e}")

    except subprocess.TimeoutExpired:
        await update.message.reply_text(
            t('commands.exec_timeout', display_action=display_action,seconds=timeout), # Message for command timeout
            parse_mode='MarkdownV2'
        )
        # Delete "Executing..." message
        try:
            await executing_msg.delete()
        except:
            pass

    except Exception as e:
        # (ma l'errore 'e' viene comunque passato)
        logger.error(f"Error executing custom command {display_action}: {e}")
        e_escaped = escape_markdown(str(e), version=2)
        await update.message.reply_text(
            t('commands.exec_unexpected_error', error=e_escaped), # Message for unexpected error
            parse_mode='MarkdownV2'
        )
        # Delete "Executing..." message
        try:
            await executing_msg.delete()
        except:
            pass
