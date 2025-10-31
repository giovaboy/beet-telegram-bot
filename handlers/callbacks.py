"""
Handler for inline button callbacks (refactored for unified import schema)
"""
import time
from multiprocessing import context
import subprocess
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from telegram.helpers import escape_markdown
from i18n.translations import t
from config import BEET_CONTAINER, BEET_USER, IMPORT_PATH, DIFF_STYLE, setup_logging
from core.directory_analyzer import analyze_directory, get_search_query
from core.parsers import clean_ansi_codes, format_difference_with_diff
from ui.keyboards import (
    create_directory_list_keyboard,
    create_directory_details_keyboard,
    create_delete_confirm_keyboard,
    create_back_keyboard,
    create_import_status_keyboard
)
from ui.messages import format_directory_details, format_file_list, format_import_status
from handlers.commands import format_and_send_import_status, cleanup_old_status_message

logger = setup_logging()


# --- Utility -----------------------------------------------------------------

async def safe_delete_message(bot, chat_id, message_id):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug(f"Could not delete message {message_id}: {e}")

def get_selected_dir(manager, idx):
    dirs = manager.get_import_directories()
    return dirs[idx] if idx < len(dirs) else None


# --- Core Dispatcher ----------------------------------------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    query = update.callback_query
    await query.answer()
    action = query.data or ""
    idx = None

    logger.debug(f"Callback action: {action}")

    # ✅ Confirm import - prendi ID da context, NON dal callback
    if action == "confirm_import":
        await confirm_import_from_context(query, context, manager)
        return

    prefix_map = {
        "cancel_file_list": cancel_file_list,
        "refresh_list": handle_refresh_list,
        "import_": show_directory_details,
        "files_": show_file_list,
        "images_": show_images,
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
    dirs = manager.get_import_directories()

    path_str = IMPORT_PATH
    path_escaped = escape_markdown(path_str, version=2)
    text = t('commands.list_header', count=len(dirs)) if dirs else t('commands.list_empty', path=path_escaped)

    keyboard = create_directory_list_keyboard(dirs) if dirs else None
    try:
        await query.edit_message_text(text, parse_mode='MarkdownV2', reply_markup=keyboard)
        await query.answer(t('list.refreshed'))
    except BadRequest as e:
        if "Message is not modified" in str(e):
            await query.answer(t('list.no_changes'))
        else:
            raise


async def show_directory_details(query, idx, context, manager):
    """Show details for a selected directory"""

    # 🔧 Se il messaggio era una richiesta di conferma (delete/annulla), cancellalo del tutto
    # try:
    #     await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
    #     logger.debug(f"Deleted previous confirmation message (id: {query.message.message_id})")
    # except Exception as e:
    #     logger.debug(f"Could not delete message: {e}")

    selected = get_selected_dir(manager, idx)
    if not selected:
        await query.message.reply_text(t('directory.not_available'))
        return

    # --- ESCAPE AGGIUNTO ---
    name_escaped = escape_markdown(selected.name, version=2)
    msg_analyzing = await query.message.reply_text(t('directory.analyzing', name=name_escaped), parse_mode='MarkdownV2')

    structure = analyze_directory(str(selected))
    context.user_data.update({'current_dir_idx': idx, 'current_dir_structure': structure})

    msg = format_directory_details(selected.name, structure)
    keyboard = create_directory_details_keyboard(idx, structure, get_search_query(str(selected)))
    #await query.message.reply_text(msg, parse_mode='MarkdownV2', reply_markup=keyboard)

    try:
        await query.edit_message_text(
            text=msg,
            parse_mode='MarkdownV2',
            reply_markup=keyboard
        )
        logger.debug("Replaced confirmation message with directory details")
    except Exception as e:
        logger.debug(f"Could not edit message, sending new one: {e}")
        await query.message.reply_text(msg, parse_mode='MarkdownV2', reply_markup=keyboard)


    await safe_delete_message(context.bot, query.message.chat.id, msg_analyzing.message_id)


async def cancel_file_list(query, idx, context, manager):
    """Dismiss/hide the directory list"""
    try:
        # Delete the message entirely
        await query.message.delete()
        logger.info(f"Directory list message {query.message.message_id} dismissed")

        # Clear the saved message_id from context
        context.user_data.pop('list_message_id', None)

        # Optional: send a confirmation (or just silently dismiss)
        # await query.message.reply_text(t('list.dismissed'))

    except Exception as e:
        logger.error(f"Error dismissing list: {e}")
        # Fallback: just remove keyboard
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e2:
            logger.error(f"Could not remove keyboard either: {e2}")

async def show_file_list(query, idx, context, manager):
    structure = context.user_data.get('current_dir_structure')
    if not structure:
        await query.answer(t('directory.data_unavailable'))
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    msg = format_file_list(structure)
    keyboard = create_back_keyboard(idx)
    MAX = 4000
    parts = [msg[i:i+MAX] for i in range(0, len(msg), MAX)]
    sent_ids = []

    for i, part in enumerate(parts):
        reply = await query.message.reply_text(part, parse_mode='MarkdownV2', reply_markup=keyboard if i == len(parts) - 1 else None)
        sent_ids.append(reply.message_id)

    context.user_data['file_list_message_ids'] = sent_ids


async def show_images(query, idx, context, manager):
    structure = context.user_data.get('current_dir_structure')
    if not structure:
        await query.answer(t('directory.data_unavailable'))
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

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
        return

    await query.message.reply_text(t('directory.sending_images', count=len(images)), parse_mode='MarkdownV2')
    for img in images[:10]:
        try:
            with open(img['path'], 'rb') as f:
                caption = f"{img.get('disc', '') + ': ' if 'disc' in img else ''}{img['name']}"
                # Nessun parse_mode qui, quindi nessun escape necessario per la caption
                await query.message.reply_photo(photo=f, caption=caption)
        except Exception as e:
            logger.error(f"Error sending image: {e}")

    if len(images) > 10:
        await query.message.reply_text(t('directory.images_limited', count=len(images)))

    final = await query.message.reply_text(t('directory.images_sent'), reply_markup=create_back_keyboard(idx))
    context.user_data['images_final_message_id'] = final.message_id


async def confirm_delete(query, idx, context, manager):
    dirs = manager.get_import_directories()
    name = dirs[idx].name if idx < len(dirs) else "Unknown"

    # --- ESCAPE AGGIUNTO ---
    name_escaped = escape_markdown(name, version=2)

    await query.edit_message_text(
        t('delete.confirm', name=name_escaped),
        parse_mode='MarkdownV2',
        reply_markup=create_delete_confirm_keyboard(idx, name)
    )


async def delete_directory(query, idx, context, manager):
    selected = get_selected_dir(manager, idx)
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
    await safe_delete_message(context.bot, query.message.chat.id, query.message.message_id)
    for key in ['images_final_message_id', 'file_list_message_id']:
        if key in context.user_data:
            await safe_delete_message(context.bot, query.message.chat.id, context.user_data[key])
            del context.user_data[key]
    if 'file_list_message_ids' in context.user_data:
        for mid in context.user_data['file_list_message_ids']:
            await safe_delete_message(context.bot, query.message.chat.id, mid)
        del context.user_data['file_list_message_ids']

    selected = get_selected_dir(manager, idx)
    if not selected:
        await query.message.reply_text(t('directory.not_available'))
        return

    structure = context.user_data.get('current_dir_structure')
    if not structure:
        # --- ESCAPE AGGIUNTO ---
        name_escaped = escape_markdown(selected.name, version=2)
        msg = await query.message.reply_text(t('directory.analyzing', name=name_escaped), parse_mode='MarkdownV2')
        structure = analyze_directory(str(selected))
        context.user_data['current_dir_structure'] = structure
        await safe_delete_message(context.bot, query.message.chat.id, msg.message_id)

    # Assumiamo che format_directory_details gestisca i suoi escape
    msg = format_directory_details(selected.name, structure)
    keyboard = create_directory_details_keyboard(idx, structure, get_search_query(str(selected)))
    await query.message.reply_text(msg, parse_mode='MarkdownV2', reply_markup=keyboard)

# --- Import Flow --------------------------------------------------------------

async def start_import(query, idx, context, manager):
    """Start the beet import and show results using new unified schema"""
    selected = get_selected_dir(manager, idx)
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
