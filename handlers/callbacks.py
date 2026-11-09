"""
Handler for inline button callbacks (refactored for unified import schema)
"""
import time
import subprocess
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from telegram.helpers import escape_markdown
from i18n.translations import t
from config import BEET_CONTAINER, BEET_USER, IMPORT_PATH, DIFF_STYLE, setup_logging
from core.directory_analyzer import analyze_directory, get_search_query, get_relative_path
from core.parsers import clean_ansi_codes, format_difference_with_diff
from ui.keyboards import (
    create_directory_list_keyboard,
    create_directory_details_keyboard,
    create_delete_confirm_keyboard,
    create_back_keyboard,
    create_import_status_keyboard
)
from ui.messages import format_directory_details, format_file_list, format_import_status, get_file_icon
from core.utils import is_within_import_path
from handlers.commands import (
    format_and_send_import_status,
    cleanup_old_status_message,
    check_allowed_user,
    remember_dir_list,
)

logger = setup_logging()
MAX_METADATA_FILES = 40

# --- Utility -----------------------------------------------------------------

async def safe_delete_message(bot, chat_id, message_id):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug(f"Could not delete message {message_id}: {e}")

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


# --- Core Dispatcher ----------------------------------------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    query = update.callback_query
    if not check_allowed_user(update, context):
        user = update.effective_user
        logger.warning(f"Unauthorized callback from user {user.id if user else '?'}: {query.data!r}")
        await query.answer(t('status.access_denied'), show_alert=True)
        return
    await query.answer()
    action = query.data or ""
    idx = None
    file_idx = None

    logger.debug(f"Callback action: {action}")

    # ✅ Confirm import - prendi ID da context, NON dal callback
    if action == "confirm_import":
        await confirm_import_from_context(query, context, manager)
        return

    # 🎯 NEW: Handle metadata file download
    if action.startswith("download_meta_"):
        parts = action.split("_")
        if len(parts) >= 4:
            idx = int(parts[2])
            file_idx = int(parts[3])
            await download_metadata_file(query, idx, file_idx, context, manager)
            return

    prefix_map = {
        "cancel_file_list": cancel_file_list,
        "refresh_list": handle_refresh_list,
        "import_": show_directory_details,
        "files_": show_file_list,
        "images_": show_images,
        "metadata_": show_metadata,
        "back_": handle_back_button,
        "confirm_delete_": confirm_delete,
        "delete_final_": delete_directory,
        "start_import_": start_import,
        "match_": handle_match_select
    }

    # Handle prefix-based actions
    for prefix, handler in prefix_map.items():
        if action.startswith(prefix):
            parts = action.split("_")
            for p in parts:
                if p.isdigit():
                    idx = int(p)
                    break
            await handler(query, idx, context, manager)
            return

    # Handle simple actions
    if not manager.current_import:
        await query.message.reply_text(t('directory.not_available'))
        return

    actions_map = {
        "cancel_import": cancel_current_import,
        "skip": skip_import,
        "retry": retry_import,
        "search_more": search_more,
        "info": show_info,
        "mb_id": ask_mb_id,
        "discogs_id": ask_discogs_id,
        "as_is": ask_confirm_as_is,
        #"force_import": force_import,
        "cancel_preview": cancel_preview,
        "single_match_accept": single_match_accept,
    }

    handler = actions_map.get(action)
    if handler:
        await handler(query, context, manager)

# --- Action handlers ----------------------------------------------------------

async def handle_refresh_list(query, idx, context, manager):
    """
    Refresh directory list when user clicks [Refresh] button.
    
    UX Strategy:
    - DELETE current list message
    - CREATE new message at bottom of chat
    - Keep it fresh and clean!
    """
    chat_id = query.message.chat.id
    
    # 🗑️ Step 1: Delete current message
    await safe_delete_message(context.bot, chat_id, query.message.message_id)
    logger.debug(f"Deleted old list message {query.message.message_id}")
    
    # 📝 Step 2: Get updated directory list
    dirs = manager.get_import_directories()
    remember_dir_list(context, dirs)

    path_str = IMPORT_PATH
    path_escaped = escape_markdown(path_str, version=2)
    
    if dirs:
        text = t('commands.list_header', count=len(dirs))
        keyboard = create_directory_list_keyboard(dirs)
    else:
        text = t('commands.list_empty', path=path_escaped)
        keyboard = None
    
    # 📩 Step 3: Send NEW message at bottom of chat
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode='MarkdownV2',
        reply_markup=keyboard
    )
    
    # 💾 Step 4: Update context with new message ID
    context.user_data['list_message_id'] = sent.message_id
    logger.debug(f"✅ Created refreshed list message: {sent.message_id}")
    
    # ✅ Acknowledge action
    await query.answer(t('list.refreshed'))


async def show_directory_details(query, idx, context, manager):
    """
    Show details for a selected directory.

    UX Strategy:
    - EDIT the list message to show directory details
    - If analyzing takes time, show temporary spinner
    - Replace list content + keyboard with details
    """

    selected = get_selected_dir(context, idx)
    if not selected:
        await query.answer(t('directory.not_available'), show_alert=True)
        return

    # 💾 Save that we're now in "details" mode
    context.user_data['current_dir_idx'] = idx
    context.user_data['in_directory_details'] = True

    # Check if we need to analyze (not in cache)
    structure = context.user_data.get('current_dir_structure')
    needs_analysis = (
        not structure or
        context.user_data.get('cached_dir_path') != str(selected)
    )

    if needs_analysis:
        # 🔄 Show temporary "Analyzing..." state
        name_escaped = escape_markdown(selected.name, version=2)
        analyzing_text = t('directory.analyzing', name=name_escaped)

        try:
            await query.edit_message_text(
                analyzing_text,
                parse_mode='MarkdownV2',
                reply_markup=None  # Remove keyboard during analysis
            )
        except Exception as e:
            logger.debug(f"Could not show analyzing state: {e}")

        # 🔍 Perform analysis
        structure = analyze_directory(str(selected))
        context.user_data['current_dir_structure'] = structure
        context.user_data['cached_dir_path'] = str(selected)

    # 📝 Build details message
    # Get parent path for nested albums
    from config import IMPORT_PATH
    from core.directory_analyzer import get_relative_path

    root_path = Path(IMPORT_PATH)
    rel_path = get_relative_path(selected, root_path)

    # Extract parent if nested (e.g., "Jazz / Miles Davis" → parent="Jazz")
    parent_path = None
    if '/' in rel_path:
        parts = rel_path.split(' / ')
        if len(parts) > 1:
            parent_path = ' / '.join(parts[:-1])  # Everything except last part

    logger.debug(f"Building details for: {selected.name}, parent: {parent_path}")

    msg = format_directory_details(selected.name, structure, parent_path)

    # 🎯 DEBUG: Log message before sending
    logger.debug(f"Message length: {len(msg)} chars")
    if len(msg) < 500:
        logger.debug(f"Message preview: {msg[:500]}")

    keyboard = create_directory_details_keyboard(
        idx,
        structure,
        get_search_query(str(selected))
    )

    # 🔄 EDIT message to show details (replaces list OR analyzing spinner)
    try:
        await query.edit_message_text(
            text=msg,
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )
        await query.answer()  # Acknowledge click
        logger.debug(f"✅ Showing directory details for idx={idx}")

    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            # Already showing these details
            await query.answer(t('directory.already_showing'))
        else:
            logger.error(f"Failed to show directory details: {e}")
            await query.answer("⚠️ Failed to load details", show_alert=True)
            raise


async def cancel_file_list(query, idx, context, manager):
    """
    Dismiss/cancel the directory list.

    UX Strategy:
    - DELETE the list message entirely
    - Clean up state
    - Send ephemeral confirmation
    """
    try:
        # 🗑️ Delete the list message
        await query.message.delete()
        logger.info(f"✅ Deleted list message {query.message.message_id}")

        # 🧹 Clear the saved message_id from context
        context.user_data.pop('list_message_id', None)
        context.user_data.pop('in_directory_details', None)
        context.user_data.pop('current_dir_idx', None)

        # ✅ Send ephemeral confirmation (no keyboard)
        await query.answer(t('list.dismissed'), show_alert=False)

    except Exception as e:
        logger.error(f"Error dismissing list: {e}")

        # Fallback: just remove keyboard
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer(t('list.dismissed'))
        except Exception as e2:
            logger.error(f"Could not remove keyboard either: {e2}")
            await query.answer("⚠️ Error dismissing list", show_alert=True)


async def show_file_list(query, idx, context, manager):
    """
    Show file list with proper keyboard management.

    UX Flow:
    1. REMOVE keyboard from parent message
    2. SEND file list messages
    3. ADD [Back] keyboard to last message
    """
    structure = context.user_data.get('current_dir_structure')
    if not structure:
        await query.answer(t('directory.data_unavailable'))
        return

    # 🎯 Step 1: Remove keyboard from parent (details message)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        logger.debug("Removed keyboard from details message")
    except Exception as e:
        logger.debug(f"Could not remove keyboard: {e}")

    # 🎯 Step 2: Send file list content
    msg = format_file_list(structure)
    keyboard = create_back_keyboard(idx)

    MAX = 4000
    parts = [msg[i:i+MAX] for i in range(0, len(msg), MAX)]
    sent_ids = []

    for i, part in enumerate(parts):
        # Only add keyboard to LAST message
        msg_keyboard = keyboard if i == len(parts) - 1 else None

        reply = await query.message.reply_text(
            part,
            parse_mode='MarkdownV2',
            reply_markup=msg_keyboard
        )
        sent_ids.append(reply.message_id)
        logger.debug(f"Sent file list part {i+1}/{len(parts)}: message {reply.message_id}")

    # 🎯 Step 3: Save message IDs for cleanup
    context.user_data['file_list_message_ids'] = sent_ids

    await query.answer()


async def show_images(query, idx, context, manager):
    """
    Show images with proper keyboard management.

    UX Flow:
    1. REMOVE keyboard from parent message
    2. SEND images
    3. SEND final message with [Back] keyboard
    4. CLEAN UP on back
    """
    structure = context.user_data.get('current_dir_structure')
    if not structure:
        await query.answer(t('directory.data_unavailable'))
        return

    # 🎯 Step 1: Remove keyboard from parent
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        logger.debug("Removed keyboard from details message")
    except Exception as e:
        logger.debug(f"Could not remove keyboard: {e}")

    # Collect all images
    images = []
    if structure['type'] == 'multi_disc':
        images.extend(structure.get('images', []))
        for disc in structure['discs']:
            for img in disc.get('images', []):
                img['disc'] = disc['name']
                images.append(img)
    else:
        images = structure.get('images', [])

    if not images:
        await query.answer(t('directory.no_images'))
        # Restore keyboard since we're not showing content
        selected = get_selected_dir(context, idx)
        if selected:
            keyboard = create_directory_details_keyboard(
                idx,
                structure,
                get_search_query(str(selected))
            )
            try:
                await query.edit_message_reply_markup(reply_markup=keyboard)
            except:
                pass
        return

    # 🎯 Step 2: Send "Sending images..." notification
    sending_msg = await query.message.reply_text(
        t('directory.sending_images', count=len(images)),
        parse_mode='MarkdownV2'
    )

    # 🎯 Step 3: Send images (max 10)
    sent_image_ids = []
    for img in images[:10]:
        # A downloaded "cover.jpg" may be a symlink to any file in the container
        if not is_within_import_path(img['path']):
            logger.warning(f"Skipping image outside import path: {img['path']}")
            continue
        try:
            with open(img['path'], 'rb') as f:
                caption = f"{img.get('disc', '') + ': ' if 'disc' in img else ''}{img['name']}"
                sent = await query.message.reply_photo(photo=f, caption=caption)
                sent_image_ids.append(sent.message_id)
        except Exception as e:
            logger.error(f"Error sending image {img['name']}: {e}")

    # Delete "sending..." message
    await safe_delete_message(context.bot, query.message.chat.id, sending_msg.message_id)

    # Show truncation notice if needed
    if len(images) > 10:
        truncation_msg = await query.message.reply_text(
            t('directory.images_limited', count=len(images))
        )
        sent_image_ids.append(truncation_msg.message_id)

    # 🎯 Step 4: Send final message with [Back] keyboard
    final_msg = await query.message.reply_text(
        t('directory.images_sent'),
        reply_markup=create_back_keyboard(idx)
    )

    # 🎯 Step 5: Save all message IDs for cleanup
    context.user_data['images_final_message_id'] = final_msg.message_id
    context.user_data['content_message_ids'] = sent_image_ids  # All image messages

    await query.answer()


# Add this to callbacks.py after show_images() function (around line 270)

async def show_metadata(query, idx, context, manager):
    """
    Show metadata files with download buttons.
    Files ≤30MB: downloadable
    Files >30MB: warning message
    """
    structure = context.user_data.get('current_dir_structure')
    if not structure:
        await query.answer(t('directory.data_unavailable'))
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Collect all metadata files
    metadata_files = []

    if structure['type'] == 'multi_disc':
        # Root level metadata
        metadata_files.extend(structure.get('metadata_files', []))

        # Disc level metadata
        for disc in structure['discs']:
            for meta in disc.get('metadata_files', []):
                meta['disc'] = disc['name']
                metadata_files.append(meta)
    else:
        metadata_files = structure.get('metadata_files', [])

    if not metadata_files:
        await query.answer(t('directory.no_metadata'))
        return

    # Sort by size (largest first) and limit to 15
    metadata_files.sort(key=lambda x: x['size'], reverse=True)
    shown_files = metadata_files[:15]

    # Build message
    total_count = len(metadata_files)
    msg = f"📄 *{t('directory.metadata_header', count=total_count)}*\n\n"

    # Create inline keyboard with file buttons
    keyboard = []

    for i, meta in enumerate(shown_files):
        size_mb = meta['size'] / (1024 * 1024)
        size_kb = meta['size'] / 1024

        # Format size
        if size_mb >= 1:
            size_str = f"{size_mb:.1f} MB"
        else:
            size_str = f"{size_kb:.1f} KB"

        # Icon based on extension
        icon = get_file_icon(meta['ext'])

        # Disc label if present
        disc_label = f"[{meta.get('disc', '')}] " if 'disc' in meta else ""

        # Truncate filename for button
        name_display = meta['name']
        if len(name_display) > 35:
            name_display = name_display[:32] + "..."

        button_text = f"{icon} {disc_label}{name_display} ({size_str})"

        # Callback includes file index
        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=f"download_meta_{idx}_{i}"
            )
        ])

    # Show truncation notice if needed
    if total_count > 15:
        remaining = total_count - 15
        msg += f"_\\({t('directory.showing_first', count=15)} of {total_count}\\)_\n\n"

    # Add back button
    keyboard.append([
        InlineKeyboardButton(t('buttons.back'), callback_data=f"back_{idx}")
    ])

    await query.message.reply_text(
        msg,
        parse_mode='MarkdownV2',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Save file list in context for download handler
    context.user_data['metadata_files_list'] = shown_files
    context.user_data['metadata_message_id'] = query.message.message_id


async def download_metadata_file(query, idx, file_idx, context, manager):
    """
    Download a metadata file if ≤50MB, otherwise show warning.
    DEBUG VERSION with extensive logging.
    """
    logger.info(f"=== download_metadata_file START ===")
    logger.info(f"idx={idx}, file_idx={file_idx}")

    await query.answer()

    # Get file list from context
    metadata_files = context.user_data.get('metadata_files_list', [])
    logger.info(f"Found {len(metadata_files)} files in context")

    if file_idx >= len(metadata_files):
        logger.error(f"file_idx {file_idx} out of range (max: {len(metadata_files)-1})")
        await query.message.reply_text("❌ File not found in list")
        return

    meta = metadata_files[file_idx]
    logger.info(f"Selected file: {meta['name']} ({meta['size']} bytes)")

    size_mb = meta['size'] / (1024 * 1024)
    logger.info(f"Size: {size_mb:.2f} MB")

    # Check size limit
    MAX_SIZE_MB = 50

    if size_mb > MAX_SIZE_MB:
        logger.warning(f"File too large: {size_mb:.2f} MB > {MAX_SIZE_MB} MB")

        # Show warning with Back button
        name_escaped = escape_markdown(meta['name'], version=2)
        size_escaped = escape_markdown(f"{size_mb:.1f}", version=2)
        limit_escaped = escape_markdown(str(MAX_SIZE_MB), version=2)

        warning_msg = (
            f"⚠️ *{t('metadata.file_too_large')}*\n\n"
            f"📄 {name_escaped}\n"
            f"📊 Size: {size_escaped} MB\n"
            f"⚠️ Limit: {limit_escaped} MB\n\n"
            f"{t('metadata.exceeds_limit')}"
        )

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                t('buttons.back_to_metadata'),
                callback_data=f"metadata_{idx}"
            )
        ]])

        sent = await query.message.reply_text(
            warning_msg,
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )

        logger.info(f"Sent warning message: {sent.message_id}")

        warning_msgs = context.user_data.get('metadata_warning_ids', [])
        warning_msgs.append(sent.message_id)
        context.user_data['metadata_warning_ids'] = warning_msgs

        logger.info(f"=== download_metadata_file END (too large) ===")
        return

    # File is within limit - send it
    logger.info("File size OK, proceeding with download")

    # A downloaded "info.txt" may be a symlink to any file in the container
    # (e.g. /proc/self/environ, which holds the bot token): never follow it out
    if not is_within_import_path(meta['path']):
        logger.warning(f"Refusing to send file outside import path: {meta['path']}")
        await query.message.reply_text("❌ File not available")
        return

    try:
        # Show "sending..." message
        logger.info("Sending 'sending...' message")
        sending_msg = await query.message.reply_text(
            f"📤 {t('metadata.sending_file')}..."
        )
        logger.info(f"Sending message ID: {sending_msg.message_id}")

        # Send file
        logger.info(f"Opening file: {meta['path']}")
        with open(meta['path'], 'rb') as f:
            size_str = f"{size_mb:.1f} MB" if size_mb >= 1 else f"{meta['size']/1024:.1f} KB"
            disc_label = f"[{meta.get('disc', '')}] " if 'disc' in meta else ""
            caption = f"{disc_label}{meta['name']} ({size_str})"

            logger.info(f"Sending document with caption: {caption}")
            sent_file = await query.message.reply_document(
                document=f,
                filename=meta['name'],
                caption=caption
            )
            logger.info(f"Document sent, message ID: {sent_file.message_id}")

        # Delete "sending..." message
        logger.info(f"Deleting 'sending...' message {sending_msg.message_id}")
        await safe_delete_message(
            context.bot,
            query.message.chat.id,
            sending_msg.message_id
        )
        logger.info("'Sending...' message deleted")

        # 🎯 Send confirmation message with Back button
        logger.info("Sending confirmation message with Back button")

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                t('buttons.back_to_metadata'),
                callback_data=f"metadata_{idx}"
            )
        ]])

        logger.info(f"Keyboard created: {keyboard}")

        confirmation_msg = await query.message.reply_text(
            f"✅ {t('metadata.file_sent')}",
            reply_markup=keyboard
        )

        logger.info(f"Confirmation sent, message ID: {confirmation_msg.message_id}")
        logger.info(f"Confirmation has keyboard: {confirmation_msg.reply_markup is not None}")

        # Track sent file and confirmation for cleanup
        sent_files = context.user_data.get('metadata_sent_files', [])
        sent_files.append(sent_file.message_id)
        sent_files.append(confirmation_msg.message_id)
        context.user_data['metadata_sent_files'] = sent_files

        logger.info(f"Tracked sent files: {sent_files}")
        logger.info(f"=== download_metadata_file END (success) ===")

    except Exception as e:
        logger.error(f"ERROR during file send: {e}", exc_info=True)
        error_escaped = escape_markdown(str(e), version=2)

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                t('buttons.back_to_metadata'),
                callback_data=f"metadata_{idx}"
            )
        ]])

        await query.message.reply_text(
            f"❌ *{t('metadata.send_error')}*\n\n{error_escaped}",
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )
        logger.info(f"=== download_metadata_file END (error) ===")


async def confirm_delete(query, idx, context, manager):
    selected = get_selected_dir(context, idx)
    if not selected:
        await query.edit_message_text(t('directory.not_available'))
        return
    name = selected.name

    # --- ESCAPE AGGIUNTO ---
    name_escaped = escape_markdown(name, version=2)

    await query.edit_message_text(
        t('delete.confirm', name=name_escaped),
        parse_mode='MarkdownV2',
        reply_markup=create_delete_confirm_keyboard(idx, name)
    )


async def delete_directory(query, idx, context, manager):
    selected = get_selected_dir(context, idx)
    if not selected:
        await query.edit_message_text(t('directory.not_available'))
        return

    result = manager.delete_directory(str(selected))
    if result['status'] != 'success':
        key = f"delete.{result['message']}" if result['message'] in ['error_root', 'error_not_found'] else 'delete.error'
        await query.edit_message_text(t(key, error=result.get('message', '')))
        return

    # ✅ Mostra solo il messaggio finale (directory eliminata)
    await query.edit_message_text(t('delete.success', name=result['message']))

    # Pulisce lo stato del manager
    manager.clear_state()

    # Poi aggiorna la lista directory in un nuovo messaggio (se necessario)
    # dirs = manager.get_import_directories()
    # if not dirs:
    #     path_escaped = escape_markdown(IMPORT_PATH, version=2)
    #     await query.message.reply_text(
    #         t('commands.list_empty', path=path_escaped),
    #         parse_mode='MarkdownV2'
    #     )
    # else:
    #     msg = t('commands.list_header', count=len(dirs))
    #     await query.message.reply_text(
    #         msg,
    #         parse_mode='MarkdownV2',
    #         reply_markup=create_directory_list_keyboard(dirs)
    #     )

# --- Navigation ---------------------------------------------------------------

async def handle_back_button(query, idx, context, manager):
    """
    Handle [Back] button navigation.

    UX Strategy:
    - If in directory details → EDIT back to list
    - If in content view (files/images/metadata) → DELETE content + RESTORE parent keyboard
    - Clean up temporary state
    """

    # 🧹 Determine what content needs cleanup
    has_content_messages = any([
        context.user_data.get('content_message_ids'),
        context.user_data.get('images_final_message_id'),
        context.user_data.get('file_list_message_ids'),
        context.user_data.get('metadata_message_id'),
    ])

    if has_content_messages:
        # We're in content view → go back to directory details
        await back_to_directory_details_with_cleanup(query, idx, context, manager)
    else:
        # We're in directory details → go back to list
        await back_to_list(query, context, manager)


async def back_to_directory_details_with_cleanup(query, idx, context, manager):
    """
    Navigate back to directory details from content view.

    UX Flow:
    1. DELETE all content messages (images, file list, metadata)
    2. DELETE the "Back" message itself
    3. RESTORE keyboard on the parent details message
    """
    chat_id = query.message.chat.id

    # 🗑️ Step 1: Delete all content messages

    # Generic content messages
    content_msg_ids = context.user_data.pop('content_message_ids', [])
    for msg_id in content_msg_ids:
        await safe_delete_message(context.bot, chat_id, msg_id)
        logger.debug(f"Deleted content message {msg_id}")

    # Images final message (the one with Back button)
    if images_msg_id := context.user_data.pop('images_final_message_id', None):
        await safe_delete_message(context.bot, chat_id, images_msg_id)
        logger.debug(f"Deleted images final message {images_msg_id}")

    # File list messages (can be multiple)
    if file_list_ids := context.user_data.pop('file_list_message_ids', None):
        for msg_id in file_list_ids:
            await safe_delete_message(context.bot, chat_id, msg_id)
            logger.debug(f"Deleted file list message {msg_id}")

    # Metadata message
    if metadata_msg_id := context.user_data.pop('metadata_message_id', None):
        await safe_delete_message(context.bot, chat_id, metadata_msg_id)
        logger.debug(f"Deleted metadata message {metadata_msg_id}")

    # 🎯 NEW: Metadata warning messages (file too large)
    if warning_ids := context.user_data.pop('metadata_warning_ids', None):
        for msg_id in warning_ids:
            await safe_delete_message(context.bot, chat_id, msg_id)
            logger.debug(f"Deleted metadata warning {msg_id}")

    # 🎯 NEW: Metadata sent files (downloaded files + confirmations)
    if sent_files := context.user_data.pop('metadata_sent_files', None):
        for msg_id in sent_files:
            await safe_delete_message(context.bot, chat_id, msg_id)
            logger.debug(f"Deleted metadata sent file {msg_id}")

    # 🗑️ Step 2: Delete the Back button message itself (current message)
    await safe_delete_message(context.bot, chat_id, query.message.message_id)
    logger.debug(f"Deleted back button message {query.message.message_id}")

    # 🔄 Step 3: Get directory structure and restore keyboard
    selected = get_selected_dir(context, idx)
    if not selected:
        # Directory no longer exists - go back to list
        await send_message_to_chat(context.bot, chat_id, t('directory.not_available'))
        await back_to_list_via_chat(context, manager, chat_id)
        return

    # Get or re-analyze structure
    structure = context.user_data.get('current_dir_structure')
    if not structure or context.user_data.get('cached_dir_path') != str(selected):
        # Re-analyze if needed
        structure = analyze_directory(str(selected))
        context.user_data['current_dir_structure'] = structure
        context.user_data['cached_dir_path'] = str(selected)

    # Build keyboard
    keyboard = create_directory_details_keyboard(
        idx,
        structure,
        get_search_query(str(selected))
    )

    # 🔄 Step 4: RESTORE keyboard on parent message
    parent_msg_id = context.user_data.get('list_message_id')

    if parent_msg_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=parent_msg_id,
                reply_markup=keyboard
            )
            logger.debug(f"✅ Restored keyboard on parent message {parent_msg_id}")

        except Exception as e:
            logger.error(f"Failed to restore keyboard on message {parent_msg_id}: {e}")

            # Fallback: Send new details message
            from config import IMPORT_PATH
            from core.directory_analyzer import get_relative_path

            root_path = Path(IMPORT_PATH)
            rel_path = get_relative_path(selected, root_path)

            parent_path = None
            if '/' in rel_path:
                parts = rel_path.split(' / ')
                if len(parts) > 1:
                    parent_path = ' / '.join(parts[:-1])

            msg = format_directory_details(selected.name, structure, parent_path)

            sent = await send_message_to_chat(
                context.bot,
                chat_id,
                msg,
                parse_mode='MarkdownV2',
                reply_markup=keyboard
            )
            context.user_data['list_message_id'] = sent.message_id
            logger.debug(f"Created new details message {sent.message_id}")
    else:
        # No parent message found - create new one
        logger.warning("No parent message ID found, creating new details message")

        from config import IMPORT_PATH
        from core.directory_analyzer import get_relative_path

        root_path = Path(IMPORT_PATH)
        rel_path = get_relative_path(selected, root_path)

        parent_path = None
        if '/' in rel_path:
            parts = rel_path.split(' / ')
            if len(parts) > 1:
                parent_path = ' / '.join(parts[:-1])

        msg = format_directory_details(selected.name, structure, parent_path)

        sent = await send_message_to_chat(
            context.bot,
            chat_id,
            msg,
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )
        context.user_data['list_message_id'] = sent.message_id
        logger.debug(f"Created new details message {sent.message_id}")


async def send_message_to_chat(bot, chat_id, text, **kwargs):
    """Helper to send message to chat (not as reply to query)"""
    return await bot.send_message(chat_id=chat_id, text=text, **kwargs)


async def back_to_list_via_chat(context, manager, chat_id):
    """Helper to go back to list when we don't have a query"""
    dirs = manager.get_import_directories()
    remember_dir_list(context, dirs)

    if not dirs:
        from config import IMPORT_PATH
        from telegram.helpers import escape_markdown
        path_escaped = escape_markdown(IMPORT_PATH, version=2)
        text = t('commands.list_empty', path=path_escaped)
        keyboard = None
    else:
        text = t('commands.list_header', count=len(dirs))
        keyboard = create_directory_list_keyboard(dirs)

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode='MarkdownV2',
        reply_markup=keyboard
    )

    # Update state
    context.user_data['list_message_id'] = sent.message_id
    context.user_data.pop('in_directory_details', None)
    context.user_data.pop('current_dir_idx', None)


async def back_to_list(query, context, manager):
    """
    Navigate back to directory list from details.

    UX: EDIT current message to show list
    """
    dirs = manager.get_import_directories()
    remember_dir_list(context, dirs)

    if not dirs:
        path_escaped = escape_markdown(IMPORT_PATH, version=2)
        text = t('commands.list_empty', path=path_escaped)
        keyboard = None
    else:
        text = t('commands.list_header', count=len(dirs))
        keyboard = create_directory_list_keyboard(dirs)

    try:
        # 🔄 EDIT to list
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )

        # 🧹 Clean directory state
        context.user_data.pop('in_directory_details', None)
        context.user_data.pop('current_dir_idx', None)
        # Keep structure cached for fast re-entry

        await query.answer()
        logger.debug("✅ Navigated back to list")

    except Exception as e:
        logger.error(f"Failed to navigate back to list: {e}")
        await query.answer("⚠️ Navigation failed", show_alert=True)


async def back_to_directory_details(query, idx, context, manager):
    """
    Navigate back to directory details from content view.

    UX: DELETE current message + restore details in parent
    """
    # 🗑️ Delete the current "Back" message (content view)
    await safe_delete_message(context.bot, query.message.chat.id, query.message.message_id)

    selected = get_selected_dir(context, idx)
    if not selected:
        await query.message.reply_text(t('directory.not_available'))
        return

    # Get cached structure
    structure = context.user_data.get('current_dir_structure')
    if not structure:
        # Re-analyze if needed
        name_escaped = escape_markdown(selected.name, version=2)
        msg = await query.message.reply_text(
            t('directory.analyzing', name=name_escaped),
            parse_mode='MarkdownV2'
        )
        structure = analyze_directory(str(selected))
        context.user_data['current_dir_structure'] = structure
        await safe_delete_message(context.bot, query.message.chat.id, msg.message_id)

    # 🔄 Find and EDIT the details message (should be list_message_id)
    details_msg_id = context.user_data.get('list_message_id')

    if details_msg_id:
        msg = format_directory_details(selected.name, structure)
        keyboard = create_directory_details_keyboard(
            idx,
            structure,
            get_search_query(str(selected))
        )

        try:
            await context.bot.edit_message_text(
                chat_id=query.message.chat.id,
                message_id=details_msg_id,
                text=msg,
                parse_mode='MarkdownV2',
                reply_markup=keyboard
            )
            logger.debug("✅ Restored directory details")
        except Exception as e:
            logger.error(f"Could not restore details: {e}")
            # Fallback: send new message
            await query.message.reply_text(
                msg,
                parse_mode='MarkdownV2',
                reply_markup=keyboard
            )
    else:
        # No parent message found, create new
        msg = format_directory_details(selected.name, structure)
        keyboard = create_directory_details_keyboard(
            idx,
            structure,
            get_search_query(str(selected))
        )
        sent = await query.message.reply_text(
            msg,
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )
        context.user_data['list_message_id'] = sent.message_id

# --- Import Flow --------------------------------------------------------------

async def cleanup_browse_keyboards(context, chat_id):
    """
    Clean up all browse mode keyboards and messages when import starts.
    
    This switches from BROWSE mode to IMPORT mode.
    
    Cleans:
    - List message keyboard
    - Directory details keyboard
    - Content view messages (files, images, metadata)
    - All temporary messages
    
    Result: Clean slate for import mode
    """
    logger.info("🧹 Cleaning up browse mode keyboards")
    
    # 🗑️ Remove keyboard from list/details message (keep message, remove keyboard)
    list_msg_id = context.user_data.get('list_message_id')
    if list_msg_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=list_msg_id,
                reply_markup=None
            )
            logger.debug(f"Removed keyboard from list message {list_msg_id}")
        except Exception as e:
            logger.debug(f"Could not remove keyboard from list message: {e}")
    
    # 🗑️ Delete all content view messages
    
    # File list messages
    if file_list_ids := context.user_data.pop('file_list_message_ids', None):
        for msg_id in file_list_ids:
            await safe_delete_message(context.bot, chat_id, msg_id)
            logger.debug(f"Deleted file list message {msg_id}")
    
    # Images messages
    if content_ids := context.user_data.pop('content_message_ids', None):
        for msg_id in content_ids:
            await safe_delete_message(context.bot, chat_id, msg_id)
            logger.debug(f"Deleted image message {msg_id}")
    
    if images_final := context.user_data.pop('images_final_message_id', None):
        await safe_delete_message(context.bot, chat_id, images_final)
        logger.debug(f"Deleted images final message {images_final}")
    
    # Metadata messages
    if metadata_msg := context.user_data.pop('metadata_message_id', None):
        await safe_delete_message(context.bot, chat_id, metadata_msg)
        logger.debug(f"Deleted metadata message {metadata_msg}")
    
    if sent_files := context.user_data.pop('metadata_sent_files', None):
        for msg_id in sent_files:
            await safe_delete_message(context.bot, chat_id, msg_id)
            logger.debug(f"Deleted metadata sent file {msg_id}")
    
    if warnings := context.user_data.pop('metadata_warning_ids', None):
        for msg_id in warnings:
            await safe_delete_message(context.bot, chat_id, msg_id)
            logger.debug(f"Deleted metadata warning {msg_id}")
    
    # 🧹 Clear browse state
    context.user_data.pop('in_directory_details', None)
    context.user_data.pop('current_dir_idx', None)
    context.user_data.pop('current_dir_structure', None)
    context.user_data.pop('cached_dir_path', None)
    context.user_data.pop('metadata_files_list', None)
    
    logger.info("✅ Browse mode cleanup complete")


async def start_import(query, idx, context, manager):
    """
    Start the beet import and show results using new unified schema.
    
    This transitions from BROWSE mode to IMPORT mode:
    1. Clean up all browse keyboards
    2. Start import
    3. Show import status
    """
    selected = get_selected_dir(context, idx)
    if not selected:
        await query.message.reply_text(t('directory.not_available'))
        return
    
    # 🧹 STEP 1: Clean up browse mode
    await cleanup_browse_keyboards(context, query.message.chat.id)
    
    # 🎯 STEP 2: Show "starting import..." message
    name_escaped = escape_markdown(selected.name, version=2)
    msg_start = await query.message.reply_text(
        t('import.starting', name=name_escaped), 
        parse_mode='MarkdownV2'
    )

    try:
        # 🎯 STEP 3: Start import
        result = manager.start_import(str(selected))
        manager.current_import = result
        manager.save_state()

        # 🎯 STEP 4: Show import status (IMPORT mode)
        mid = await format_and_send_import_status(query.message, result, manager, context)
        context.user_data['last_status_message_id'] = mid
        
    except Exception as e:
        logger.error(f"Error during import: {e}")
        error_escaped = escape_markdown(str(e), version=2)
        await query.message.reply_text(
            t('import.start_error', error=error_escaped), 
            parse_mode='MarkdownV2'
        )
    
    # 🗑️ Delete "starting import..." message
    await safe_delete_message(context.bot, query.message.chat.id, msg_start.message_id)


async def start_import1(query, idx, context, manager):
    """Start the beet import and show results using new unified schema"""
    selected = get_selected_dir(context, idx)
    if not selected:
        await query.message.reply_text(t('directory.not_available'))
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # --- ESCAPE AGGIUNTO ---
    name_escaped = escape_markdown(selected.name, version=2)
    msg_start = await query.message.reply_text(t('import.starting', name=name_escaped), parse_mode='MarkdownV2')

    try:
        result = manager.start_import(str(selected))
        manager.current_import = result
        manager.save_state()

        # 🧩 Use unified formatting
        mid = await format_and_send_import_status(query.message, result, manager, context)
        context.user_data['last_status_message_id'] = mid
    except Exception as e:
        logger.error(f"Error during import: {e}")
        # --- ESCAPE AGGIUNTO ---
        error_escaped = escape_markdown(str(e), version=2)
        await query.message.reply_text(t('import.start_error', error=error_escaped), parse_mode='MarkdownV2')
    await safe_delete_message(context.bot, query.message.chat.id, msg_start.message_id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FLOW B1: Accept Automatic Single Match (Direct, No Preview)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def single_match_accept(query, context, manager):
    """
    FLOW B1: Accept the automatic single match from start_import().

    - Used ONLY when user clicks "Accept Match" on initial automatic match
    - Uses: manager.current_import["single_match"]
    - Does NOT check context.user_data (that's for manual previews)
    """
    logger.debug("FLOW B1: Single match accept (direct)")

    current = manager.current_import
    single_match = current.get("single_match")

    if not single_match:
        await query.answer(t('errors.single_match_not_found'))
        return

    # Extract ID from state (not context!)
    mb_id = single_match.get("mb_id")
    discogs_id = single_match.get("discogs_id")

    if not mb_id and not discogs_id:
        await query.answer(t('errors.no_id_for_match'))
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Prefer MusicBrainz over Discogs
    if mb_id:
        logger.info(f"Accepting automatic single match - MBID: {mb_id}")
        await import_with_mb_id(query=query, update=None, mb_id=mb_id, context=context, manager=manager, auto_apply=True)
    elif discogs_id:
        logger.info(f"Accepting automatic single match - Discogs ID: {discogs_id}")
        await import_with_discogs_id(query=query, update=None, discogs_id=discogs_id, context=context, manager=manager, auto_apply=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FLOW C: Select Candidate → Preview → Confirm
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def handle_match_select(query, idx, context, manager):
    """
    FLOW C1: User selects a candidate from multiple matches.

    - Shows preview first (auto_apply=False)
    - Saves ID in context.user_data
    - User must then click "Confirm Import" to proceed
    """
    logger.debug(f"FLOW C1: Candidate selection - index {idx}")

    current = manager.current_import

    # Verify we're in the right state
    if current.get('status') != 'has_candidates':
        await query.answer("⚠️ Import state changed, please restart")
        return

    candidates = current.get("candidates", [])

    if not candidates or idx is None or idx >= len(candidates):
        await query.answer(t('errors.match_not_found'))
        return

    cand = candidates[idx]

    # ✅ Validate ID from callback matches candidate
    callback_parts = query.data.split("_")
    if len(callback_parts) >= 3:
        id_from_callback = callback_parts[2]

        mb_id = cand.get("mb_id")
        discogs_id = cand.get("discogs_id")

        expected_id = (mb_id[:8] if mb_id else None) or (discogs_id[:8] if discogs_id else None)

        if id_from_callback != "none" and expected_id and not expected_id.startswith(id_from_callback):
            logger.warning(f"ID mismatch! Callback: {id_from_callback}, Expected: {expected_id}")
            await query.answer("⚠️ Candidate list changed, please select again")
            return

    # ✅ Clean old pending IDs
    context.user_data.pop('pending_import_id', None)
    context.user_data.pop('pending_import_source', None)

    # Save selection to state
    manager.current_import["selected_index"] = idx
    manager.save_state()

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Show preview (auto_apply=False)
    mb_id = cand.get("mb_id")
    if mb_id:
        logger.info(f"Showing preview for candidate #{idx} - MBID: {mb_id}")
        await import_with_mb_id(query=query, update=None, mb_id=mb_id, context=context, manager=manager, auto_apply=False)
        return

    discogs_id = cand.get("discogs_id")
    if discogs_id:
        logger.info(f"Showing preview for candidate #{idx} - Discogs ID: {discogs_id}")
        await import_with_discogs_id(query=query, update=None, discogs_id=discogs_id, context=context, manager=manager, auto_apply=False)
        return

    # No ID available
    await query.message.reply_text(t('errors.no_id_for_candidate'))



async def confirm_import_with_source(query, source: str, id_value: str, context, manager):
    """Confirm import with explicit source - final implementation."""

    if not manager.current_import:
        await query.answer(t('errors.no_import_active'))
        return

    # Validate source
    if source not in ['mb', 'discogs']:
        logger.error(f"Invalid source in callback: {source}")
        await query.answer(t('errors.invalid_source'))
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug(f"Could not remove keyboard: {e}")

    # Format confirmation message
    id_escaped = escape_markdown(id_value, version=2)
    source_name = "MusicBrainz" if source == "mb" else "Discogs"

    await query.message.reply_text(
        t("import.with_source_id", source=source_name, id=id_escaped),
        parse_mode='MarkdownV2'
    )

    # Execute import
    try:
        if source == 'mb':
            result = manager.import_with_id(
                manager.current_import["path"],
                id=id_value,
                auto_apply=True
            )
        else:  # discogs
            result = manager.import_with_id(
                manager.current_import["path"],
                id=id_value,
                auto_apply=True
            )
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        await query.message.reply_text(
            f"❌ Import failed: {escape_markdown(str(e), version=2)}",
            parse_mode='MarkdownV2'
        )
        return

    # Handle result
    if result.get("status") == "success":
        await cleanup_old_status_message(context, query.message.chat.id, 'completed_label')
        await query.message.reply_text(
            t('status.import_completed'),
            parse_mode='MarkdownV2'
        )
        manager.clear_state()
    else:
        error_msg = result.get('message', 'Unknown error')
        error_escaped = escape_markdown(error_msg, version=2)
        await query.message.reply_text(
            t('status.generic_error_preview', error=error_escaped),
            parse_mode='MarkdownV2'
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Import With ID Helpers (Used by all flows)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def import_with_mb_id(query=None, update=None, mb_id=None, context=None, manager=None, auto_apply=False):
    """
    Execute import with MusicBrainz ID.

    - auto_apply=False: Show preview (FLOWS C1, D1)
    - auto_apply=True: Direct import (FLOWS B1, confirmation)
    """
    message = None
    # ── Determina la sorgente dell'update ────────────────────────────
    if query:
        await query.answer()
        message = query.message
    elif update and update.message:
        message = update.message
    else:
        return

    #id_escaped = escape_markdown(mb_id, version=2)

    if not auto_apply:
        await message.reply_text(t("import.with_mb_id"), parse_mode='MarkdownV2')

    result = manager.import_with_id(
        manager.current_import["path"],
        id=mb_id,
        auto_apply=auto_apply
    )

    if result["status"] == "needs_confirmation":
        # Show preview (this will save ID in context)
        await show_import_preview(query=query, update=update, id=mb_id, result=result, context=context, manager=manager, id_type='mb')
        return

    if result["status"] == "success":
        await cleanup_old_status_message(context, message.chat.id, 'completed_label')
        await message.reply_text(t('status.import_completed'), parse_mode='MarkdownV2')
        manager.clear_state()
    else:
        await message.reply_text(
            t('status.import_error', message=result.get('message', 'Unknown error'))
        )


async def import_with_discogs_id(query=None, update=None, discogs_id=None, context=None, manager=None, auto_apply=False):
    """
    Execute import with Discogs ID.

    - auto_apply=False: Show preview (FLOWS C1, D2)
    - auto_apply=True: Direct import (FLOWS B1, confirmation)
    """
    message = None
    if query:
        await query.answer()
        message = query.message
    elif update and update.message:
        message = update.message
    else:
        return

    #id_escaped = escape_markdown(discogs_id, version=2)

    if not auto_apply:
        await message.reply_text(t("import.with_discogs_id"), parse_mode='MarkdownV2')

    result = manager.import_with_id(
        manager.current_import["path"],
        id=discogs_id,
        auto_apply=auto_apply
    )

    if result["status"] == "needs_confirmation":
        # Show preview (this will save ID in context)
        await show_import_preview(query=query, update=update, id=discogs_id, result=result, context=context, manager=manager, id_type='discogs')
        return

    if result["status"] == "success":
        await cleanup_old_status_message(context, message.chat.id, 'completed_label')
        await message.reply_text(t('status.import_completed'), parse_mode='MarkdownV2')
        manager.clear_state()
    else:
        await message.reply_text(
            t('status.import_error', message=result.get('message', 'Unknown error'))
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SHARED: Show Import Preview (Used by Flows B2, C1, D1, D2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def show_import_preview(query=None, update=None, id=None, result=None, context=None, manager=None, id_type=None):
    """
    Show import preview with confirmation button.

    Used by:
    - FLOW B2: Manual ID after automatic single match
    - FLOW C1: Selected candidate preview
    - FLOW D1: Manual MB ID entry
    - FLOW D2: Manual Discogs ID entry

    ALWAYS:
    - Saves ID in context.user_data
    - Shows "Confirm Import" button (callback: "confirm_import")
    - Does NOT modify manager.current_import
    """
    logger.debug(f"Showing preview - {id_type}:{id}")

    message = None

    # ── Determina la sorgente dell'update ────────────────────────────
    if query:
        try:
            await query.answer()
        except Exception as e:
            logger.debug(f"query.answer() failed: {e}")
        message = query.message
        logger.debug(f"Query message: {message}")
    elif update and update.message:
        message = update.message
        logger.debug(f"Update message: {message}")
    else:
        logger.debug("No valid message found — returning early!")
        return

    preview = result.get("preview", {}).get("single_match") or {}

    artist = escape_markdown(str(preview.get('artist', '?')), version=2)
    album = escape_markdown(str(preview.get('album', '?')), version=2)
    year = escape_markdown(str(preview.get('year', '?')), version=2)
    similarity = escape_markdown(str(preview.get('similarity', '?')), version=2)


    msg = f"🎯 *{t('status.preview')}*\n\n"
    msg += f"🎤 {t('fields.artist', artist=artist)}\n"
    msg += f"💿 {t('fields.album', album=album)}\n"
    msg += f"📅 {t('fields.year', year=year)}\n"
    msg += f"📊 {t('fields.similarity', similarity=similarity)}\n"

    if preview.get("differences"):
        msg += "\n⚠️ *" + t('status.differences') + "*\n"

        for diff in preview["differences"][:15]:
            formatted = format_difference_with_diff(diff, style=DIFF_STYLE)
            msg += formatted + "\n"

        if len(preview["differences"]) > 15:
            remaining = len(preview["differences"]) - 15
            msg += f"\n{t('fields.more_diff',remaining=remaining)}\n"

    # Add source link
    source_name = "MusicBrainz" if id_type == "mb" else "Discogs"
    if preview.get("mb_url") and id_type == "mb":
        msg += f"\n{t('fields.link', source=source_name, url=preview['mb_url'])}\n"
    elif preview.get("discogs_url") and id_type == "discogs":
        msg += f"\n{t('fields.link', source=source_name, url=preview['discogs_url'])}\n"

    # ✅ CRITICAL: Save ID in context for confirmation
    context.user_data['pending_import_id'] = id
    context.user_data['pending_import_source'] = id_type
    context.user_data['pending_import_ts'] = time.time()  # save timestamp
    logger.debug(f"Saved to context - pending_import_id: {id}, source: {id_type}, ts: {context.user_data['pending_import_ts']}")

    # ✅ CRITICAL: Use "confirm_import" callback (NOT "single_match_accept")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            t('buttons.confirm_import'),
            callback_data="confirm_import"  # Handler: confirm_import_from_context()
        )],
        [InlineKeyboardButton(
            t('buttons.back_to_candidates') if manager.current_import.get('candidates') else t('buttons.cancel'),
            callback_data="cancel_preview"
        )]
    ])

    # Try to edit existing preview message
    if 'preview_message_id' in context.user_data:
        try:
            await context.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=context.user_data['preview_message_id'],
                text=msg,
                parse_mode="MarkdownV2",
                reply_markup=keyboard,
                disable_web_page_preview=False
            )
            logger.debug(f"Edited existing preview message")
            return
        except Exception as e:
            logger.debug(f"Could not edit preview: {e}")
            context.user_data.pop('preview_message_id', None)

    # Create new preview message
    sent_msg = await message.reply_text(
        msg,
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
        disable_web_page_preview=False
    )
    context.user_data['preview_message_id'] = sent_msg.message_id
    logger.debug(f"Created new preview message: {sent_msg.message_id}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FLOWS B2, C1, D1, D2: Confirm Import from Preview
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TIMEOUT_PENDING_IMPORT = 2 * 60 * 60  # 2 ore

def is_pending_import_valid(context):
    ts = context.user_data.get('pending_import_ts')
    if not ts:
        return False
    return (time.time() - ts) <= TIMEOUT_PENDING_IMPORT


async def confirm_import_from_context(query, context, manager):
    """
    Confirm import using ID stored in context (from preview).

    Used by:
    - FLOW B2: Manual ID after automatic match
    - FLOW C1: Confirmed candidate selection
    - FLOW D1: Manual MB ID entry
    - FLOW D2: Manual Discogs ID entry

    - Uses: context.user_data['pending_import_id']
    - Does NOT use: manager.current_import
    """
    logger.debug("Confirming import from context")

    if not manager.current_import:
        await query.answer(t('errors.no_import_active'))
        return

    if not is_pending_import_valid(context):
        context.user_data.pop('pending_import_id', None)
        context.user_data.pop('pending_import_source', None)
        context.user_data.pop('pending_import_ts', None)
        await query.answer("⏳ La preview è scaduta, reinserisci l'ID", show_alert=True)
        return

    # ✅ Get ID from context (NOT from manager state!)
    id_value = context.user_data.pop('pending_import_id', None)
    source = context.user_data.pop('pending_import_source', None)
    context.user_data.pop('pending_import_ts', None)

    if not id_value or not source:
        logger.error("No pending import ID in context!")
        await query.answer(t('errors.no_pending_import'))
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug(f"Could not remove keyboard: {e}")

    # Show confirmation
    source_name = "MusicBrainz" if source == "mb" else "Discogs"
    id_escaped = escape_markdown(id_value[:40], version=2)

    await query.message.reply_text(
        t("import.with_source_id", source=source_name, id=id_escaped),
        parse_mode='MarkdownV2'
    )

    # ✅ Execute import with auto_apply=True
    logger.info(f"Executing import - {source}:{id_value}")

    try:
        if source == 'mb':
            result = manager.import_with_id(
                manager.current_import["path"],
                id=id_value,
                auto_apply=True
            )
        else:  # discogs
            result = manager.import_with_id(
                manager.current_import["path"],
                id=id_value,
                auto_apply=True
            )
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        await query.message.reply_text(
           t("import.generic_error_preview", error=escape_markdown(str(e), version=2)),
            parse_mode='MarkdownV2'
        )
        return

    # Handle result
    if result.get("status") == "success":
        await cleanup_old_status_message(context, query.message.chat.id, 'completed_label')
        await query.message.reply_text(t("status.success"), parse_mode='MarkdownV2')
        manager.clear_state()

        # Clean preview message reference
        context.user_data.pop('preview_message_id', None)
    else:
        error_msg = result.get('message', 'Unknown error')
        error_escaped = escape_markdown(error_msg, version=2)
        await query.message.reply_text(
            f"❌ *Import failed*\n\n{error_escaped}",
            parse_mode='MarkdownV2'
        )



async def cancel_current_import(query, context, manager):
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cleanup_old_status_message(context, query.message.chat.id, 'cancelled_label')
    await query.message.reply_text(t('commands.import_cancelled'))
    manager.clear_state()


async def skip_import(query, context, manager):
    result = manager.skip_item(manager.current_import['path'])
    await cleanup_old_status_message(context, query.message.chat.id, 'skipped_label')
    await query.message.reply_text(t('status.skipped', message=result['message']))
    manager.clear_state()


async def retry_import(query, context, manager):
    await query.message.reply_text(t('import.retrying'), parse_mode='MarkdownV2')
    await cleanup_old_status_message(context, query.message.chat.id, 'cancelled_label')
    result = manager.start_import(manager.current_import['path'])
    manager.current_import = result
    manager.save_state()
    mid = await format_and_send_import_status(query.message, result, manager, context)
    context.user_data['last_status_message_id'] = mid


async def search_more(query, context, manager):
    await query.message.reply_text(t('import.searching'))
    result = manager.search_candidates(manager.current_import['path'])
    output = clean_ansi_codes(result.get('output', t('import.no_results')))
    await query.message.reply_text(t('import.search_result', output=output[:1000]), parse_mode='Markdown')


# async def force_import(query, context, manager):
#     await query.message.reply_text(t('import.force'))
#     try:
#         beet_path = manager.translate_path_for_beet(manager.current_import['path'])
#         beet_cmd = ['beet', 'import', '-a', beet_path]
#         cmd = ['docker', 'exec', '-u', BEET_USER, BEET_CONTAINER] + beet_cmd if BEET_CONTAINER else beet_cmd
#         result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
#         if result.returncode == 0 or 'import' in (result.stdout + result.stderr).lower():
#             await cleanup_old_status_message(context, query.message.chat.id, 'completed_label')
#             await query.message.reply_text(t('status.import_completed'), parse_mode='MarkdownV2')
#             manager.clear_state()
#         else:
#             await query.message.reply_text(t('status.error_or_input', error=result.stderr[:200]))
#     except Exception as e:
#         await query.message.reply_text(t('status.generic_error', error=str(e)))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FLOW C2: Cancel Preview and Return to Candidates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def cancel_preview(query, context, manager):
    """
    FLOW C2: Cancel preview and return to candidate list.

    - Cleans context.user_data
    - Restores candidate list (from manager.current_import - still intact!)
    """
    logger.debug("Cancelling preview, returning to candidates")

    if not manager.current_import:
        await query.answer(t('errors.no_import_active'))
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # ✅ Reset selected index
    if manager.current_import.get("selected_index") is not None:
        manager.current_import["selected_index"] = None
        manager.save_state()

    # ✅ Clean all pending data from context
    context.user_data.pop('pending_import_id', None)
    context.user_data.pop('pending_import_source', None)
    context.user_data.pop('preview_message_id', None)

    logger.info("Cleaned context, restoring candidate list")

    # ✅ Show candidates again (still in manager.current_import!)
    if manager.current_import.get('candidates'):
        msg = format_import_status(manager.current_import)
        keyboard = create_import_status_keyboard(manager.current_import, context)

        await query.message.reply_text(
            msg,
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )
    else:
        # No candidates (shouldn't happen)
        await query.message.reply_text(
            "⚠️ No candidates available",
            parse_mode='MarkdownV2'
        )



# --- Input prompts ------------------------------------------------------------

async def ask_mb_id(query, context, manager):
    context.user_data['waiting_for'] = 'mb_id'
    await query.message.reply_text(t('prompts.mb_id'), parse_mode='MarkdownV2')

async def ask_discogs_id(query, context, manager):
    context.user_data['waiting_for'] = 'discogs_id'
    await query.message.reply_text(t('prompts.discogs_id'), parse_mode='MarkdownV2')

async def ask_confirm_as_is(query, context, manager):
    context.user_data['waiting_for'] = 'confirm_as_is'
    await query.message.reply_text(t('prompts.confirm_as_is'), parse_mode='MarkdownV2')


# --- Info Button --------------------------------------------------------------

async def show_info(query, context, manager):
    """Display details for current import or list of candidates"""
    current = manager.current_import
    if not current:
        await query.message.reply_text(t('info.no_data'))
        return

    # Single match
    if current.get("single_match"):
        sm = current["single_match"]

        artist = sm.get('artist')
        album = sm.get('album')
        year = sm.get('year') # Aggiungiamo anche l'anno che era null
        similarity = sm.get('similarity')

        artist_display = '?' if artist is None else artist
        album_display = '?' if album is None else album
        year_display = '?' if year is None else year # Se lo usi altrove
        similarity_display = '?' if similarity is None else similarity

        # --- ESCAPE AGGIUNTI ---
        artist_escaped = escape_markdown(artist_display, version=2)
        album_escaped = escape_markdown(album_display, version=2)
        year_escaped = escape_markdown(str(year_display), version=2)
        similarity_escaped = escape_markdown(str(similarity_display), version=2)

        text = (
            f"📖 *{t('info.release_details')}*\n\n"
            f"🎤 {artist_escaped}\n"
            f"💿 {album_escaped}\n"
            f"📊 {t('info.similarity')}: {similarity_escaped}%\n"
        )
        if sm.get("mb_url"):
            # --- ESCAPE AGGIUNTO ---
            url_escaped = escape_markdown(sm['mb_url'], version=2)
            text += t('fields.mb_link', url=url_escaped)

        await query.message.reply_text(text, parse_mode="MarkdownV2")

    # Multiple candidates
    elif current.get("candidates"):
        lines = []
        for i, c in enumerate(current["candidates"], 1):
            artist = c.get('artist')
            album = c.get('album')
            year = c.get('year') # Aggiungiamo anche l'anno che era null
            similarity = c.get('similarity')

            artist_display = '?' if artist is None else artist
            album_display = '?' if album is None else album
            year_display = '?' if year is None else year # Se lo usi altrove
            similarity_display = '?' if similarity is None else similarity
            # --- ESCAPE AGGIUNTI ---
            # Escape dei contenuti
            artist_escaped = escape_markdown(artist_display, version=2)
            album_escaped = escape_markdown(album_display, version=2)
            year_escaped = escape_markdown(str(year_display), version=2)
            similarity_escaped = escape_markdown(str(similarity_display), version=2)
            # Ricostruzione della stringa con la formattazione voluta *attorno*
            # ai contenuti con escape.
            # Aggiunto escape per il punto (lista) e le parentesi quadre (link).
            lines.append(f"{i}\. *{artist_escaped}* — _{album_escaped}_ \({year_escaped}\) \[score {similarity_escaped}\]")

        await query.message.reply_text(
            f"📖 *{t('info.candidate_list')}*\n\n" + "\n".join(lines),
            parse_mode="MarkdownV2"
        )

    else:
        await query.message.reply_text(t('info.no_data'))
