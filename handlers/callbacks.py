"""
Handler for inline button callbacks
"""
import subprocess
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from pathlib import Path
from i18n.translations import t
from config import BEET_CONTAINER, BEET_USER
from core.directory_analyzer import analyze_directory, get_search_query
from core.parsers import clean_ansi_codes
from ui.keyboards import (
    create_directory_list_keyboard,
    create_directory_details_keyboard,
    create_delete_confirm_keyboard,
    create_back_keyboard
)
from ui.messages import format_directory_details, format_file_list, format_import_status
from handlers.commands import format_and_send_import_status, cleanup_old_status_message 

logger = logging.getLogger(__name__)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Handler for buttons"""
    query = update.callback_query
    await query.answer()
    action = query.data
    
    # Refresh list
    if action == "refresh_list":
        await handle_refresh_list(query, manager)
        return
    
    # Show directory details
    if action.startswith("import_"):
        idx = int(action.split("_")[1])
        await show_directory_details(query, idx, context, manager)
        return
    
    # Show file list
    if action.startswith("files_"):
        idx = int(action.split("_")[1])
        await show_file_list(query, idx, context)
        return
    
    # Show images
    if action.startswith("images_"):
        idx = int(action.split("_")[1])
        await show_images(query, idx, context)
        return
    
    # ✅ NEW: Back button
    if action.startswith("back_"):
        idx = int(action.split("_")[1])
        await handle_back_button(query, idx, context, manager)
        return

    # Delete confirmation request
    if action.startswith("confirm_delete_"):
        idx = int(action.split("_")[2])
        await confirm_delete(query, idx, manager)
        return

    # Actual deletion
    if action.startswith("delete_final_"):
        idx = int(action.split("_")[2])
        await delete_directory(query, idx, context, manager)
        return
    
    # Start import
    if action.startswith("start_import_"):
        idx = int(action.split("_")[2])
        await start_import(query, idx, manager, context)
        return
    
    # Candidate selection (currently a simple message)
    if action.startswith("select_"):
        await query.message.reply_text(t('buttons.use_mb_button')) # Message prompting to use the specific MB button
        return

    # Selection with MusicBrainz ID
    if action.startswith("selectid_"):
        mb_id = action.split("_", 1)[1]
        await import_with_mb_id(query, mb_id, manager, context)
        return
    
    # Actions on the current import
    if not manager.current_import:
        await query.message.reply_text(t('directory.not_available')) # Message when no import is available
        return
    
    if action == "cancel_import":
        await cancel_current_import(query, context, manager)
    
    elif action == "skip":
        await skip_import(query, manager, context)
        
    elif action == "retry":
        await retry_import(query, manager, context)
    
    elif action == "search_more":
        await search_more(query, manager)
        
    elif action == "mb_id":
        context.user_data['waiting_for'] = 'mb_id'
        await query.message.reply_text(t('prompts.mb_id'), parse_mode='Markdown') # Prompt for MusicBrainz ID
        
    elif action == "discogs_id":
        context.user_data['waiting_for'] = 'discogs_id'
        await query.message.reply_text(t('prompts.discogs_id'), parse_mode='Markdown') # Prompt for Discogs ID
    
    elif action == "as_is":
        context.user_data['waiting_for'] = 'confirm_as_is'
        await query.message.reply_text(t('prompts.confirm_as_is'), parse_mode='Markdown') # Prompt for "import as is" confirmation
    
    elif action == "force_import":
        await force_import(query, manager, context)

# Helper functions

async def handle_refresh_list(query, manager):
    """Handles list refresh with visual feedback"""
    dirs = manager.get_import_directories()
    new_text = ""
    keyboard = None

    if not dirs:
        new_text = t('commands.list_empty', path='/downloads')
    else:
        new_text = t('commands.list_header', count=len(dirs))
        keyboard = create_directory_list_keyboard(dirs)

    try:
        await query.edit_message_text(
            new_text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        # ✅ Show feedback only if actually updated
        await query.answer(text=t('list.refreshed')) # Feedback for list refreshed

    except BadRequest as e:
        if "Message is not modified" in str(e):
            # ✅ Ignore error, but show different feedback
            await query.answer(text=t('list.no_changes')) # Feedback for no changes
        else:
            raise

async def show_directory_details(query, idx, context, manager):
    """Shows directory details"""
    dirs = manager.get_import_directories()
    
    if idx >= len(dirs):
        await query.message.reply_text(t('directory.not_available'))
        return
    
    selected_dir = dirs[idx]
    
    # Send analysis message and save message_id
    analyzing_msg = await query.message.reply_text(
        t('directory.analyzing', name=selected_dir.name), # Message for starting analysis
        parse_mode='Markdown'
    )
    
    # Analyze the directory
    structure = analyze_directory(str(selected_dir))
    
    context.user_data['current_dir_idx'] = idx
    context.user_data['current_dir_structure'] = structure
    
    msg = format_directory_details(selected_dir.name, structure)
    search_query = get_search_query(str(selected_dir))
    keyboard = create_directory_details_keyboard(idx, structure, search_query)
    
    # Send the message with details
    await query.message.reply_text(
        msg,
        parse_mode='Markdown',
        reply_markup=keyboard
    )
    
    # ✅ DELETE the analysis message
    try:
        await analyzing_msg.delete()
        logger.debug(f"Analysis message {analyzing_msg.message_id} deleted")
    except Exception as e:
        logger.warning(f"Could not delete analysis message: {e}")

async def show_file_list(query, idx, context):
    """Shows the file list"""
    structure = context.user_data.get('current_dir_structure')
    if not structure:
        await query.answer(t('directory.data_unavailable')) # Message for unavailable directory data
        return
    
    # ✅ REMOVE KEYBOARD from the previous message
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        logger.debug(f"Keyboard removed from message {query.message.message_id}")
    except Exception as e:
        logger.warning(f"Could not remove keyboard: {e}")
    
    msg = format_file_list(structure)
    keyboard = create_back_keyboard(idx)
    
    # ✅ LONG MESSAGE HANDLING - Telegram limit is 4096 characters
    MAX_LENGTH = 4000  # Leaving some margin
    
    if len(msg) <= MAX_LENGTH:
        # Short message, send normally
        file_msg = await query.message.reply_text(
            msg,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        # Save to delete it when "Back" is pressed
        context.user_data['file_list_message_id'] = file_msg.message_id
    else:
        # Message too long, split into parts
        parts = []
        current_part = ""
        
        for line in msg.split('\n'):
            if len(current_part) + len(line) + 1 <= MAX_LENGTH:
                current_part += line + '\n'
            else:
                if current_part:
                    parts.append(current_part)
                current_part = line + '\n'
        
        if current_part:
            parts.append(current_part)
        
        # Send all parts except the last one without a keyboard
        message_ids = []
        for i, part in enumerate(parts[:-1]):
            msg_sent = await query.message.reply_text(part, parse_mode='Markdown')
            message_ids.append(msg_sent.message_id)
        
        # Send the last part with the keyboard
        final_msg = await query.message.reply_text(
            parts[-1],
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        message_ids.append(final_msg.message_id)
        
        # Save all message_ids for deletion
        context.user_data['file_list_message_ids'] = message_ids

async def show_images(query, idx, context):
    """Shows the directory images"""
    structure = context.user_data.get('current_dir_structure')
    if not structure:
        await query.answer(t('directory.data_unavailable'))
        return
    
    all_images = []
    
    if structure['type'] == 'multi_disc':
        all_images.extend(structure.get('images', []))
        
        for disc in structure['discs']:
            for img in disc.get('images', []):
                img['disc'] = disc['name']
                all_images.append(img)
    else:
        all_images = structure.get('images', [])
    
    if not all_images:
        await query.answer(t('directory.no_images')) # Message when no images are found
        return
    
    await query.message.reply_text(t('directory.sending_images', count=len(all_images))) # Message for sending images
    
    # Limit to 10 images to avoid flooding
    for img in all_images[:10]:
        try:
            caption = img.get('disc', '') + ': ' + img['name'] if 'disc' in img else img['name']
            with open(img['path'], 'rb') as photo:
                await query.message.reply_photo(photo=photo, caption=caption)
        except Exception as e:
            logger.error(f"Error sending image: {e}")
    
    if len(all_images) > 10:
        await query.message.reply_text(t('directory.images_limited', count=len(all_images))) # Message for image limit
    
    keyboard = create_back_keyboard(idx)
    final_msg = await query.message.reply_text(t('directory.images_sent'), reply_markup=keyboard) # Message when images are sent
    context.user_data['images_final_message_id'] = final_msg.message_id

async def confirm_delete(query, idx, manager):
    """Confirms directory deletion"""
    dirs = manager.get_import_directories()
    selected_dir_name = dirs[idx].name if idx < len(dirs) else "Unknown"

    keyboard = create_delete_confirm_keyboard(idx, selected_dir_name)

    await query.edit_message_text(
        t('delete.confirm', name=selected_dir_name), # Confirmation message for deletion
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def delete_directory(query, idx, context, manager):
    """Deletes directory"""
    dirs = manager.get_import_directories()
    
    if idx >= len(dirs):
        await query.message.reply_text(t('directory.not_available'))
        return

    selected_dir = dirs[idx]
    result = manager.delete_directory(str(selected_dir))
    
    if result['status'] == 'success':
        await query.message.reply_text(t('delete.success', name=result['message'])) # Message for successful deletion
        manager.clear_state()
        
        # Return to list
        from handlers.commands import list_imports
        await list_imports(query, context, manager)
    else:
        error_key = f"delete.{result['message']}" if result['message'] in ['error_root', 'error_not_found'] else 'delete.error'
        await query.message.reply_text(t(error_key, error=result.get('message', ''))) # Generic/specific error message for deletion

async def start_import(query, idx, manager, context):
    """Starts import"""
    dirs = manager.get_import_directories()
    
    if idx >= len(dirs):
        await query.message.reply_text(t('directory.not_available'))
        return
    
    selected_dir = dirs[idx]
    
    # ✅ REMOVE KEYBOARD from the message with the "Start Import" button
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        logger.debug(f"Keyboard removed from message {query.message.message_id}")
    except Exception as e:
        logger.warning(f"Could not remove keyboard: {e}")
    
    # Send start message and save message_id
    starting_msg = await query.message.reply_text(
        t('import.starting', name=selected_dir.name), # Message for starting import
        parse_mode='Markdown'
    )
    
    try:
        # Start the import
        result = manager.start_import(str(selected_dir))
        manager.current_import = result
        manager.save_state()
        
        # Send import status and save message_id for later cleanup
        message_id = await format_and_send_import_status(query.message, result, manager, context)
        context.user_data['last_status_message_id'] = message_id
        
    except Exception as e:
        logger.error(f"Error during start_import: {e}")
        await query.message.reply_text(
            t('import.start_error', error=str(e)), # Message for import start error
            parse_mode='Markdown'
        )
    
    # ✅ DELETE the start message
    try:
        await starting_msg.delete()
        logger.debug(f"Start message {starting_msg.message_id} deleted")
    except Exception as e:
        logger.warning(f"Could not delete start message: {e}")

async def import_with_mb_id(query, mb_id, manager, context):
    """Import with MusicBrainz ID"""
    
    await query.message.reply_text(t('import.with_id', id=mb_id), parse_mode='Markdown') # Message for starting import with ID
    
    result = manager.import_with_id(manager.current_import['path'], mb_id=mb_id)
    
    if result['status'] == 'success':
        # Cleanup old message before confirming
        await cleanup_old_status_message(context, query.message.chat_id, 'completed_label')
        
        await query.message.reply_text(t('status.import_completed')) # Message for completed import
        manager.clear_state()
    else:
        await query.message.reply_text(t('status.import_error', message=result['message'])) # Message for import error

async def cancel_current_import(query, context, manager):
    """Cancels current import"""
    
    # Cleanup old message
    await cleanup_old_status_message(context, query.message.chat_id, 'cancelled_label')
    
    await query.message.reply_text(t('commands.import_cancelled')) # Message for import cancelled
    manager.clear_state()

async def skip_import(query, manager, context):
    """Skips import"""
    
    result = manager.skip_item(manager.current_import['path'])
    
    # Cleanup old message
    await cleanup_old_status_message(context, query.message.chat_id, 'skipped_label')
    
    await query.message.reply_text(t('status.skipped', message=result['message'])) # Message for skipped import
    manager.clear_state()

async def retry_import(query, manager, context):
    """Retries import"""
    
    await query.message.reply_text(t('import.retrying')) # Message for retrying import
    
    # Cleanup old message
    await cleanup_old_status_message(context, query.message.chat_id, 'cancelled_label')
    
    result = manager.start_import(manager.current_import['path'])
    manager.current_import = result
    manager.save_state()
    
    # New message with new message_id
    message_id = await format_and_send_import_status(query.message, result, manager, context)
    context.user_data['last_status_message_id'] = message_id

async def search_more(query, manager):
    """Searches for more information"""
    await query.message.reply_text(t('import.searching')) # Message for searching
    result = manager.search_candidates(manager.current_import['path'])
    output = clean_ansi_codes(result.get('output', t('import.no_results'))) # Message for no results
    await query.message.reply_text(
        t('import.search_result', output=output[:1000]), # Message for search results
        parse_mode='Markdown'
    )

async def force_import(query, manager, context):
    """Forced import"""
    
    await query.message.reply_text(t('import.force')) # Message for forced import
    try:
        beet_path = manager.translate_path_for_beet(manager.current_import['path'])
        beet_cmd = ['beet', 'import', '-a', beet_path]
        
        if BEET_CONTAINER:
            cmd = ['docker', 'exec']
            if BEET_USER:
                cmd.extend(['-u', BEET_USER])
            cmd.extend([BEET_CONTAINER] + beet_cmd)
        else:
            cmd = beet_cmd
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        output_lower = result.stdout.lower() + result.stderr.lower()
        if result.returncode == 0 or 'import' in output_lower:
            # Cleanup old message
            await cleanup_old_status_message(context, query.message.chat_id, 'completed_label')
            
            await query.message.reply_text(t('status.import_completed')) # Message for completed import
            manager.clear_state()
        else:
            await query.message.reply_text(t('status.error_or_input', error=result.stderr[:200])) # Message for error or input required
    except Exception as e:
        await query.message.reply_text(t('status.generic_error', error=str(e))) # Generic error message


async def handle_back_button(query, idx, context, manager):
    """Handles the Back button"""
    
    # ✅ DELETE the message with the "Back" button
    try:
        await query.message.delete()
        logger.debug(f"'Back' message {query.message.message_id} deleted")
    except Exception as e:
        logger.warning(f"Could not delete message: {e}")
    
    # ... [Omitted deletion logic for brevity, it remains the same structure] ...
    # Deletion logic is unchanged, just cleaning up old file list/image messages
    # as they are transient.

    # ✅ DELETE the "Images sent" message if it exists
    if 'images_final_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=context.user_data['images_final_message_id']
            )
            logger.debug(f"Final images message deleted")
            del context.user_data['images_final_message_id']
        except Exception as e:
            logger.warning(f"Could not delete final message: {e}")
    
    # ✅ DELETE file list messages if they exist (single or multiple)
    if 'file_list_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=context.user_data['file_list_message_id']
            )
            logger.debug(f"File list message deleted")
            del context.user_data['file_list_message_id']
        except Exception as e:
            logger.warning(f"Could not delete file list message: {e}")
    
    # ✅ DELETE multiple file list messages if they exist
    if 'file_list_message_ids' in context.user_data:
        for msg_id in context.user_data['file_list_message_ids']:
            try:
                await context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=msg_id
                )
                logger.debug(f"File list message {msg_id} deleted")
            except Exception as e:
                logger.warning(f"Could not delete message {msg_id}: {e}")
        del context.user_data['file_list_message_ids']
    
    # Return to directory details
    dirs = manager.get_import_directories()
    
    if idx >= len(dirs):
        await query.message.reply_text(t('directory.not_available'))
        return
    
    selected_dir = dirs[idx]
    structure = context.user_data.get('current_dir_structure')
    
    if not structure:
        # If structure is not in cache, re-analyze
        analyzing_msg = await query.message.reply_text(
            t('directory.analyzing', name=selected_dir.name), # Message for re-analyzing
            parse_mode='Markdown'
        )
        structure = analyze_directory(str(selected_dir))
        context.user_data['current_dir_structure'] = structure
        
        # Delete analysis message
        try:
            await analyzing_msg.delete()
        except:
            pass
    
    msg = format_directory_details(selected_dir.name, structure)
    search_query = get_search_query(str(selected_dir))
    keyboard = create_directory_details_keyboard(idx, structure, search_query)
    
    await query.message.reply_text(
        msg,
        parse_mode='Markdown',
        reply_markup=keyboard
    )
