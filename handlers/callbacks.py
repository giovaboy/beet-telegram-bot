"""
Handler for inline button callbacks
"""
import subprocess
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest
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
from ui.messages import format_directory_details, format_file_list
from handlers.commands import format_and_send_import_status, cleanup_old_status_message

logger = logging.getLogger(__name__)


# --- Utility helpers ----------------------------------------------------------

async def safe_delete_message(bot, chat_id, message_id):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug(f"Could not delete message {message_id}: {e}")

def get_selected_dir(manager, idx):
    dirs = manager.get_import_directories()
    return dirs[idx] if idx < len(dirs) else None


# --- Core dispatcher ----------------------------------------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    query = update.callback_query
    await query.answer()
    action = query.data or ""
    idx = None

    # --- Mapping prefix per callback che richiedono idx ---
    prefix_map = {
        "refresh_list": handle_refresh_list,
        "import_": show_directory_details,
        "files_": show_file_list,
        "images_": show_images,
        "back_": handle_back_button,
        "confirm_delete_": confirm_delete,
        "delete_final_": delete_directory,
        "start_import_": start_import,
        "select_": handle_candidate_select,
        "selectid_": handle_select_by_id,
        "match_": handle_match_select
    }

    # Gestione callback con idx
    for prefix, handler in prefix_map.items():
        if action.startswith(prefix):
            parts = action.split("_")
            for p in parts:
                if p.isdigit():
                    idx = int(p)
                    break
            await handler(query, idx, context, manager)
            return

    # --- Callback senza idx (azioni generiche su current_import) ---
    if not manager.current_import:
        await query.message.reply_text(t('directory.not_available'))
        return

    actions_map = {
        "cancel_import": cancel_current_import,
        "skip": skip_import,
        "retry": retry_import,
        "search_more": search_more,
        "mb_id": ask_mb_id,
        "discogs_id": ask_discogs_id,
        "as_is": ask_confirm_as_is,
        "force_import": force_import,
    }

    handler = actions_map.get(action)
    if handler:
        # queste callback NON hanno idx
        await handler(query, context, manager)

# --- Action handlers ----------------------------------------------------------

async def handle_refresh_list(query, idx, context, manager):
    dirs = manager.get_import_directories()
    text = t('commands.list_header', count=len(dirs)) if dirs else t('commands.list_empty', path='/downloads')
    keyboard = create_directory_list_keyboard(dirs) if dirs else None
    try:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=keyboard)
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

    msg_analyzing = await query.message.reply_text(t('directory.analyzing', name=selected.name), parse_mode='Markdown')
    structure = analyze_directory(str(selected))
    context.user_data.update({'current_dir_idx': idx, 'current_dir_structure': structure})

    msg = format_directory_details(selected.name, structure)
    keyboard = create_directory_details_keyboard(idx, structure, get_search_query(str(selected)))
    await query.message.reply_text(msg, parse_mode='Markdown', reply_markup=keyboard)
    await safe_delete_message(context.bot, query.message.chat_id, msg_analyzing.message_id)


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
        reply = await query.message.reply_text(part, parse_mode='Markdown', reply_markup=keyboard if i == len(parts) - 1 else None)
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

    await query.message.reply_text(t('directory.sending_images', count=len(images)))
    for img in images[:10]:
        try:
            with open(img['path'], 'rb') as f:
                caption = f"{img.get('disc', '') + ': ' if 'disc' in img else ''}{img['name']}"
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
    await query.edit_message_text(
        t('delete.confirm', name=name),
        parse_mode='Markdown',
        reply_markup=create_delete_confirm_keyboard(idx, name)
    )


async def delete_directory(query, idx, context, manager):
    selected = get_selected_dir(manager, idx)
    if not selected:
        await query.message.reply_text(t('directory.not_available'))
        return

    result = manager.delete_directory(str(selected))
    if result['status'] != 'success':
        key = f"delete.{result['message']}" if result['message'] in ['error_root', 'error_not_found'] else 'delete.error'
        await query.message.reply_text(t(key, error=result.get('message', '')))
        return

    await query.message.reply_text(t('delete.success', name=result['message']))
    manager.clear_state()
    dirs = manager.get_import_directories()
    if not dirs:
        await query.message.reply_text(t('commands.list_empty', path='/downloads'), parse_mode='Markdown')
    else:
        msg = t('commands.list_header', count=len(dirs))
        await query.message.reply_text(msg, parse_mode='Markdown', reply_markup=create_directory_list_keyboard(dirs))


async def start_import(query, idx, context, manager):
    selected = get_selected_dir(manager, idx)
    if not selected:
        await query.message.reply_text(t('directory.not_available'))
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    msg_start = await query.message.reply_text(t('import.starting', name=selected.name), parse_mode='Markdown')
    try:
        result = manager.start_import(str(selected))
        manager.current_import = result
        manager.save_state()
        mid = await format_and_send_import_status(query.message, result, manager, context)
        context.user_data['last_status_message_id'] = mid
    except Exception as e:
        await query.message.reply_text(t('import.start_error', error=str(e)), parse_mode='Markdown')
    await safe_delete_message(context.bot, query.message.chat_id, msg_start.message_id)


# --- Import related actions ---------------------------------------------------

async def handle_candidate_select(query, idx, context, manager):
    try:
        candidates = context.user_data.get('current_candidates', [])
        if not (candidates and 0 < idx <= len(candidates)):
            await query.answer("Candidate not found")
            return
        cand = candidates[idx - 1]
        if cand.get('mb_id'):
            await import_with_mb_id(query, cand['mb_id'], context, manager, auto_apply=True)
        else:
            await query.message.reply_text(t('buttons.use_mb_button'))
    except Exception as e:
        logger.error(f"Error selecting candidate: {e}")
        await query.message.reply_text("Error selecting candidate")


async def handle_select_by_id(query, idx, context, manager):
    mb_id = query.data.split("_", 1)[1]
    await import_with_mb_id(query, mb_id, context, manager)


async def handle_match_select(query, idx, context, manager):
    mb_id = context.user_data.get('matches', {}).get(query.data)
    if mb_id:
        await import_with_mb_id(query, mb_id, context, manager, True)
    else:
        await query.answer(t('errors.match_not_found'))


async def import_with_mb_id(query, mb_id, context, manager, auto_apply=False):
    await query.message.reply_text(t('import.with_id', id=mb_id), parse_mode='Markdown')
    result = manager.import_with_id(manager.current_import['path'], mb_id=mb_id, auto_apply=auto_apply)
    if result['status'] == 'success':
        await cleanup_old_status_message(context, query.message.chat_id, 'completed_label')
        await query.message.reply_text(t('status.import_completed'))
        manager.clear_state()
    else:
        await query.message.reply_text(t('status.import_error', message=result['message']))


async def cancel_current_import(query, context, manager):
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
            await query.message.reply_text(t('status.import_completed'))
            manager.clear_state()
        else:
            await query.message.reply_text(t('status.error_or_input', error=result.stderr[:200]))
    except Exception as e:
        await query.message.reply_text(t('status.generic_error', error=str(e)))


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
        msg = await query.message.reply_text(t('directory.analyzing', name=selected.name), parse_mode='Markdown')
        structure = analyze_directory(str(selected))
        context.user_data['current_dir_structure'] = structure
        await safe_delete_message(context.bot, query.message.chat_id, msg.message_id)

    msg = format_directory_details(selected.name, structure)
    keyboard = create_directory_details_keyboard(idx, structure, get_search_query(str(selected)))
    await query.message.reply_text(msg, parse_mode='Markdown', reply_markup=keyboard)