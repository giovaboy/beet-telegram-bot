"""
Handler for the bot's commands
"""
import logging
import subprocess
import os
import tempfile
from telegram import Update
from telegram.ext import ContextTypes
from i18n.translations import t
from config import IMPORT_PATH, TELEGRAM_CHAT_ID, CUSTOM_COMMANDS, BEET_CONTAINER, BEET_USER
from ui.keyboards import create_directory_list_keyboard, create_import_status_keyboard
from ui.messages import format_import_status

logger = logging.getLogger(__name__)

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
        parse_mode='Markdown'
    )

async def list_imports(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Shows the list of directories to import"""
    logger.info(f"list_imports called")
    if not check_allowed_user(update, context):
        await update.message.reply_text(t('status.access_denied'))
        return
    
    logger.info("User check passed")
    dirs = manager.get_import_directories()
    logger.info(f"Found {len(dirs)} directories")
    
    if not dirs:
        await update.message.reply_text(
            t('commands.list_empty', path=IMPORT_PATH), # Message when the import list is empty
            parse_mode='Markdown'
        )
        return
    
    keyboard = create_directory_list_keyboard(dirs)
    message = t('commands.list_header', count=len(dirs)) # Header for the list of directories
    
    # Attempt to update the existing message if present
    if 'list_message_id' in context.user_data:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['list_message_id'],
                text=message,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
            return
        except Exception as e:
            logger.debug(f"Could not update existing message: {e}")
    
    # Send new message
    msg = await update.message.reply_text(
        message,
        parse_mode='Markdown',
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
        manager.clear_state()
        await update.message.reply_text(t('commands.import_cancelled')) # Message for import cancelled
    else:
        await update.message.reply_text(t('commands.no_import_to_cancel')) # Message when no import to cancel

async def format_and_send_import_status(message, result, manager, context=None):
    """Helper to format and send import status"""
    msg = format_import_status(result)
    keyboard = create_import_status_keyboard(result, context)
    
    sent_message = await message.reply_text(
        msg,
        parse_mode='Markdown',
        reply_markup=keyboard,
        disable_web_page_preview=False
    )
    
    # Save message_id for later cleanup
    if context:
        context.user_data['last_status_message_id'] = sent_message.message_id
    
    return sent_message.message_id

async def cleanup_old_status_message(context, chat_id, label_key='completed_label'):
    """Removes keyboard and adds history label to the previous message"""
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
    # Send temporary message and save the reference
    executing_msg = await update.message.reply_text(
        t('commands.executing_custom', display_action=display_action), # Message for command execution start
        parse_mode='Markdown'
    )

    try:
        result = subprocess.run(
            action, 
            capture_output=True, 
            text=True, 
            timeout=300 # 5 minutes timeout for long commands
        )
        
        # Initialize final output with success status
        output = t('commands.custom_executed', display_action=display_action) # Message for command executed (header)
        
        # Add stderr (Error Output, even if command exits with code 0)
        if result.stderr:
            # Truncate stderr if too long
            stderr_preview = result.stderr[:500]
            if len(result.stderr) > 500:
                stderr_preview += t('commands.truncated') # Message for truncation
            output += t('commands.error_stderr', stderr_preview=stderr_preview) # Message for stderr
        
        # Add stdout (The actual command result)
        if result.stdout:
            msg_body = f"```\n{result.stdout}\n```"
        else:
            msg_body = t('commands.empty_output') # Message for empty output
        
        # Combine header with body
        final_message = output + msg_body

        # ✅ LONG MESSAGE HANDLING
        MAX_LENGTH = 4000  # Telegram limit is 4096. Using 4000 for safety.
        
        if len(final_message) > MAX_LENGTH:
            # Message too long, send header and output as a text file
            await update.message.reply_text(
                t('commands.output_too_long', display_action=display_action), # Message for output too long
                parse_mode='Markdown'
            )
            
            # Create unique temporary file name
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
            
            # Send result as a text file
            with open(temp_filename, "rb") as f:
                await update.message.reply_document(
                    document=f, 
                    filename=f"{command_name}_output.txt",
                    caption=t('commands.file_caption', display_action=display_action) # Caption for the output file
                )
            
            # Clean up temporary file
            try:
                os.remove(temp_filename)
            except Exception as e:
                logger.error(f"Could not delete temporary output file: {e}")
            
        else:
            # Short message, send normally
            await update.message.reply_text(final_message, parse_mode='Markdown')
        
        # ✅ DELETE the "Executing..." message
        try:
            await executing_msg.delete()
            logger.debug(f"Command execution message {executing_msg.message_id} deleted")
        except Exception as e:
            logger.warning(f"Could not delete execution message: {e}")

    except subprocess.TimeoutExpired:
        await update.message.reply_text(
            t('commands.timeout_error', display_action=display_action) # Message for command timeout
        )
        # Delete "Executing..." message
        try:
            await executing_msg.delete()
        except:
            pass
            
    except Exception as e:
        # Catch any other Python or subprocess error
        logger.error(f"Error executing custom command {display_action}: {e}")
        await update.message.reply_text(
            t('commands.unexpected_error', error=str(e)) # Message for unexpected error
        )
        # Delete "Executing..." message
        try:
            await executing_msg.delete()
        except:
            pass
