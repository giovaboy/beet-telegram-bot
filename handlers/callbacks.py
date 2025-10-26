"""
Handler for inline button callbacks (refactored for unified import schema)
"""
import subprocess
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from telegram.helpers import escape_markdown
from i18n.translations import t
from config import BEET_CONTAINER, BEET_USER, IMPORT_PATH, setup_logging
from core.directory_analyzer import analyze_directory, get_search_query
from core.parsers import clean_ansi_codes
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
        "match_": handle_match_select,      # 🧩 semplificato
        "confirm_import_": confirm_import_handler,
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
        "force_import": force_import,
        "cancel_preview": cancel_preview,
        "single_match_accept": single_match_accept
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
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    selected = get_selected_dir(manager, idx)
    if not selected:
        await query.message.reply_text(t('directory.not_available'))
        return

    # --- ESCAPE AGGIUNTO ---
    name_escaped = escape_markdown(selected.name, version=2)
    msg_analyzing = await query.message.reply_text(t('directory.analyzing', name=name_escaped), parse_mode='MarkdownV2')

    structure = analyze_directory(str(selected))
    context.user_data.update({'current_dir_idx': idx, 'current_dir_structure': structure})

    # Assumiamo che format_directory_details gestisca i suoi escape interni
    # passando il nome *grezzo*.
    msg = format_directory_details(selected.name, structure)
    keyboard = create_directory_details_keyboard(idx, structure, get_search_query(str(selected)))
    await query.message.reply_text(msg, parse_mode='MarkdownV2', reply_markup=keyboard)
    await safe_delete_message(context.bot, query.message.chat_id, msg_analyzing.message_id)

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

    # Assumiamo che format_file_list restituisca una stringa già formattata
    # e con escape corretto per MarkdownV2.
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
        await query.message.reply_text(t('directory.not_available'))
        return

    result = manager.delete_directory(str(selected))
    if result['status'] != 'success':
        # Nessun parse_mode, nessun escape
        key = f"delete.{result['message']}" if result['message'] in ['error_root', 'error_not_found'] else 'delete.error'
        await query.message.reply_text(t(key, error=result.get('message', '')))
        return

    # Nessun parse_mode, nessun escape
    await query.message.reply_text(t('delete.success', name=result['message']))
    manager.clear_state()
    dirs = manager.get_import_directories()
    if not dirs:
        # --- ESCAPE AGGIUNTO ---
        path_str = '/downloads'
        path_escaped = escape_markdown(path_str, version=2)
        await query.message.reply_text(t('commands.list_empty', path=path_escaped), parse_mode='MarkdownV2')
    else:
        msg = t('commands.list_header', count=len(dirs))
        await query.message.reply_text(msg, parse_mode='MarkdownV2', reply_markup=create_directory_list_keyboard(dirs))


# --- Navigation ---------------------------------------------------------------

async def handle_back_button(query, idx, context, manager):
    await safe_delete_message(context.bot, query.message.chat_id, query.message.message_id)
    for key in ['images_final_message_id', 'file_list_message_id']:
        if key in context.user_data:
            await safe_delete_message(context.bot, query.message.chat_id, context.user_data[key])
            del context.user_data[key]
    if 'file_list_message_ids' in context.user_data:
        for mid in context.user_data['file_list_message_ids']:
            await safe_delete_message(context.bot, query.message.chat_id, mid)
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
        await safe_delete_message(context.bot, query.message.chat_id, msg.message_id)

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
    await safe_delete_message(context.bot, query.message.chat_id, msg_start.message_id)


async def single_match_accept(query, context, manager):
    """Handles acceptance of single match directly from button"""
    current = manager.current_import
    single_match = current.get("single_match")
    if not single_match:
        await query.answer(t('errors.single_match_not_found'))
        return

    mb_id = single_match.get("mb_id")
    if not mb_id:
        await query.message.reply_text(t('errors.no_mb_id'))
        return

    # Start preview (auto_apply=False)
    await import_with_mb_id(query, mb_id, context, manager, auto_apply=True)

async def handle_match_select(query, idx, context, manager):
    """Triggered when user selects one of the candidates"""
    current = manager.current_import
    candidates = current.get("candidates", [])
    if not candidates or idx is None or idx >= len(candidates):
        await query.answer(t('errors.match_not_found'))
        return

    # 🧩 Save selected candidate in manager state
    manager.current_import["selected_index"] = idx
    manager.save_state()

    cand = candidates[idx]
    mb_id = cand.get("mb_id")
    if not mb_id:
        await query.message.reply_text(t('errors.no_mb_id'))
        return

    # Start preview (auto_apply=False)
    await import_with_mb_id(query, mb_id, context, manager, auto_apply=False)


async def import_with_mb_id(query, mb_id, context, manager, auto_apply=False):
    """Handles import with given MBID, supporting preview and confirmation"""

    # --- ESCAPE AGGIUNTO ---
    # Gli MBID contengono trattini, che sono speciali in MarkdownV2
    id_escaped = escape_markdown(mb_id, version=2)
    await query.message.reply_text(t('import.with_id', id=id_escaped), parse_mode='MarkdownV2')

    result = manager.import_with_id(manager.current_import["path"], mb_id=mb_id, auto_apply=auto_apply)

    if result["status"] == "needs_confirmation":
        await show_import_preview(query, mb_id, result, context, manager)
        return

    if result["status"] == "success":
        await cleanup_old_status_message(context, query.message.chat_id, 'completed_label')
        await query.message.reply_text(t('status.import_completed'), parse_mode='MarkdownV2')
        manager.clear_state()
    else:
        # Nessun parse_mode, nessun escape
        await query.message.reply_text(t('status.import_error', message=result.get('message', 'Unknown error')))


async def show_import_preview(query, mb_id, result, context, manager):
    """Show import preview details"""
    preview = result.get("preview", {}).get("single_match") or {}

    artist = preview.get('artist')
    album = preview.get('album')
    year = preview.get('year') # Aggiungiamo anche l'anno che era null
    similarity = preview.get('similarity')

    artist_display = '?' if artist is None else artist
    album_display = '?' if album is None else album
    year_display = '?' if year is None else year
    similarity_display = '?' if similarity is None else similarity

    # --- ESCAPE AGGIUNTI ---
    # Artista, Album, Similarità e Diff possono contenere caratteri speciali
    artist_escaped = escape_markdown(artist_display, version=2)
    album_escaped = escape_markdown(album_display, version=2)
    year_escaped = escape_markdown(str(year_display), version=2)
    similarity_escaped = escape_markdown(str(similarity_display), version=2)

    msg = "🎯 *Import Preview*\n\n"
    msg += f"🎤 *Artist:* {artist_escaped}\n"
    msg += f"💿 *Album:* {album_escaped}\n"
    msg += f"📊 *Similarity:* {similarity_escaped}%\n"

    if preview.get("differences"):
        msg += "\n⚠️ *Differences:*\n"
        for diff in preview["differences"][:50]:
            diff_escaped = escape_markdown(diff, version=2)
            msg += f"  • {diff_escaped}\n"

    if preview.get("mb_url"):
        # --- ESCAPE AGGIUNTO ---
        # L'URL deve subire l'escape per parentesi o altri caratteri
        url_escaped = escape_markdown(preview['mb_url'], version=2)
        msg += t('fields.mb_link', url=url_escaped)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('buttons.confirm_import'), callback_data=f"confirm_import_{mb_id[:8]}")],
        [InlineKeyboardButton(t('buttons.cancel_preview'), callback_data="cancel_preview")]
    ])

    await query.message.reply_text(msg, parse_mode="MarkdownV2", reply_markup=keyboard)
    context.user_data['pending_mb_id'] = mb_id


async def confirm_import_handler(query, idx, context, manager):
    """Finalize import confirmation (auto_apply=True)."""
    mb_id = context.user_data.pop('pending_mb_id', None)
    if not mb_id:
        await query.answer(t('errors.mb_id_not_found'))
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Nessuna variabile, nessun escape
    await query.message.reply_text(t('import.confirming'), parse_mode='MarkdownV2')
    result = manager.import_with_id(manager.current_import["path"], mb_id=mb_id, auto_apply=True)

    if result.get("status") == "success":
        await query.message.reply_text(t('status.import_completed'), parse_mode='MarkdownV2')
        manager.clear_state()
    else:
        # Nessun parse_mode, nessun escape
        await query.message.reply_text(t('status.import_error', message=result.get('message', 'Unknown error')))

async def cancel_current_import(query, context, manager):
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cleanup_old_status_message(context, query.message.chat_id, 'cancelled_label')
    await query.message.reply_text(t('commands.import_cancelled'))
    manager.clear_state()


async def skip_import(query, context, manager):
    result = manager.skip_item(manager.current_import['path'])
    await cleanup_old_status_message(context, query.message.chat_id, 'skipped_label')
    await query.message.reply_text(t('status.skipped', message=result['message']))
    manager.clear_state()


async def retry_import(query, context, manager):
    await query.message.reply_text(t('import.retrying'))
    await cleanup_old_status_message(context, query.message.chat_id, 'cancelled_label')
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


async def force_import(query, context, manager):
    await query.message.reply_text(t('import.force'))
    try:
        beet_path = manager.translate_path_for_beet(manager.current_import['path'])
        beet_cmd = ['beet', 'import', '-a', beet_path]
        cmd = ['docker', 'exec', '-u', BEET_USER, BEET_CONTAINER] + beet_cmd if BEET_CONTAINER else beet_cmd
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 or 'import' in (result.stdout + result.stderr).lower():
            await cleanup_old_status_message(context, query.message.chat_id, 'completed_label')
            await query.message.reply_text(t('status.import_completed'), parse_mode='MarkdownV2')
            manager.clear_state()
        else:
            await query.message.reply_text(t('status.error_or_input', error=result.stderr[:200]))
    except Exception as e:
        await query.message.reply_text(t('status.generic_error', error=str(e)))

async def cancel_preview(query, context, manager):
    """Annulla la preview e torna alla lista dei candidati."""
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await query.message.reply_text(t('import.preview_cancelled'), parse_mode='MarkdownV2')

    # Se abbiamo ancora candidati, li mostriamo di nuovo
    if manager.current_import and manager.current_import.get('candidates'):
        from ui.keyboards import create_import_status_keyboard
        from ui.messages import format_import_status

        msg = format_import_status(manager.current_import)
        keyboard = create_import_status_keyboard(manager.current_import, context)
        await query.message.reply_text(msg, parse_mode='MarkdownV2', reply_markup=keyboard)

    # Pulizia
    context.user_data.pop('pending_mb_id', None)

# --- Input prompts ------------------------------------------------------------

async def ask_mb_id(query, context, manager):
    context.user_data['waiting_for'] = 'mb_id'
    await query.message.reply_text(t('prompts.mb_id'), parse_mode='Markdown')

async def ask_discogs_id(query, context, manager):
    context.user_data['waiting_for'] = 'discogs_id'
    await query.message.reply_text(t('prompts.discogs_id'), parse_mode='Markdown')

async def ask_confirm_as_is(query, context, manager):
    context.user_data['waiting_for'] = 'confirm_as_is'
    await query.message.reply_text(t('prompts.confirm_as_is'), parse_mode='Markdown')


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
            lines.append(f"{i}\. *{artist_escaped}* — _{album_escaped}_ ({year_escaped}) \[score {similarity_escaped}\]")

        await query.message.reply_text(
            f"📖 *{t('info.candidate_list')}*\n\n" + "\n".join(lines),
            parse_mode="MarkdownV2"
        )

    else:
        await query.message.reply_text(t('info.no_data'))
