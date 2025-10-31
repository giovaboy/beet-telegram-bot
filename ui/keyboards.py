"""
Creation of inline keyboards for Telegram (refactored for new unified import model)
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from i18n.translations import t
from core.plugin_detector import has_discogs_plugin


def create_directory_list_keyboard(dirs):
    """Creates keyboard with directory list"""
    keyboard = []
    for i, d in enumerate(dirs[:100]):
        size = sum(f.stat().st_size for f in d.rglob('*') if f.is_file())
        size_mb = size / (1024 * 1024)
        button_text = f"📁 {d.name[:35]} ({size_mb:.0f} MB)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"import_{i}")])

    keyboard.append([
        InlineKeyboardButton(t('buttons.refresh'), callback_data="refresh_list"),
        InlineKeyboardButton(t('buttons.cancel'), callback_data="cancel_file_list")
    ])
    return InlineKeyboardMarkup(keyboard)


def create_directory_details_keyboard(idx, structure, search_query):
    """Creates keyboard for directory details"""
    keyboard = []

    # Images
    total_images = len(structure.get('images', []))
    if structure['type'] == 'multi_disc':
        for disc in structure['discs']:
            total_images += len(disc.get('images', []))

    if total_images > 0:
        keyboard.append([
            InlineKeyboardButton(t('buttons.view_images', count=total_images), callback_data=f"images_{idx}"),
            InlineKeyboardButton(t('buttons.view_files'), callback_data=f"files_{idx}")
        ])
    else:
        keyboard.append([InlineKeyboardButton(t('buttons.view_files'), callback_data=f"files_{idx}")])

    # Search buttons - adapt based on enabled plugins
    search_row = []

    # MusicBrainz is always available (default source)
    mb_url = f"https://musicbrainz.org/search?query={search_query}&type=release&method=indexed"
    search_row.append(InlineKeyboardButton(t('buttons.search_mb'), url=mb_url))

    # Discogs only if plugin is enabled
    if has_discogs_plugin():
        discogs_url = f"https://www.discogs.com/search/?q={search_query}&type=release"
        search_row.append(InlineKeyboardButton(t('buttons.search_discogs'), url=discogs_url))

    keyboard.append(search_row)

    # Delete / Back / Import
    keyboard.append([InlineKeyboardButton(t('buttons.delete'), callback_data=f"confirm_delete_{idx}")])
    keyboard.append([
        InlineKeyboardButton(t('buttons.back_to_list'), callback_data="refresh_list"),
        InlineKeyboardButton(t('buttons.start_import'), callback_data=f"start_import_{idx}")
    ])
    return InlineKeyboardMarkup(keyboard)


def create_delete_confirm_keyboard(idx, dir_name):
    """Creates the deletion confirmation keyboard"""
    keyboard = [
        [InlineKeyboardButton(t('buttons.confirm_delete', name=dir_name), callback_data=f"delete_final_{idx}")],
        [InlineKeyboardButton(t('buttons.cancel'), callback_data=f"import_{idx}")]
    ]
    return InlineKeyboardMarkup(keyboard)


def create_import_status_keyboard(result, context=None):
    """
    Creates keyboard for import results.

    Flow B: Single Match → "Accept Match" button (direct accept, no preview)
    Flow C: Multiple Candidates → Numbered buttons (show preview first)
    Flow A/D: No match/Manual → Manual input buttons
    """
    keyboard = []

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FLOW B: Single Match (Direct Accept)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if result.get('single_match'):
        single_match = result['single_match']
        similarity = single_match.get('similarity', '?')

        # ✅ Direct accept button - no preview
        keyboard.append([
            InlineKeyboardButton(
                t('buttons.accept_match', similarity=similarity),
                callback_data="single_match_accept"  # Handler: single_match_accept()
            )
        ])

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FLOW C: Multiple Candidates (Preview First)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif result.get('candidates'):
        candidates = result['candidates'][:10]
        num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']

        for i, cand in enumerate(candidates):
            # Handle None values
            similarity = cand.get('similarity') or '?'
            artist = cand.get('artist') or '?'
            album = cand.get('album') or '?'
            year = cand.get('year') or '?'

            # Build label with truncation
            label = f"{artist} — {album} ({year})"
            if len(label) > 45:
                label = label[:42] + "..."

            emoji = num_emoji[i] if i < len(num_emoji) else f"{i+1}."

            # Extract ID for validation
            id_short = None
            if cand.get('mb_id'):
                id_short = cand['mb_id'][:8]
            elif cand.get('discogs_id'):
                id_short = cand['discogs_id'][:8]

            # Build callback with index and ID validation
            if id_short:
                callback_data = f"match_{i}_{id_short}"
            else:
                callback_data = f"match_{i}_none"

            # ✅ Candidate button - shows preview first
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} ({similarity}%) {label}",
                    callback_data=callback_data  # Handler: handle_match_select()
                )
            ])

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FLOW D: Manual Input Options (Always Available)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    manual_row = []

    # MusicBrainz always available
    manual_row.append(InlineKeyboardButton(t('buttons.mb_id'), callback_data="mb_id"))

    # Discogs only if plugin enabled
    if has_discogs_plugin():
        manual_row.append(InlineKeyboardButton(t('buttons.discogs_id'), callback_data="discogs_id"))

    keyboard.append(manual_row)

    keyboard.append([
        InlineKeyboardButton(t('buttons.import_as_is'), callback_data="as_is")
    ])
    keyboard.append([
        InlineKeyboardButton(t('buttons.skip'), callback_data="skip"),
        InlineKeyboardButton(t('buttons.retry'), callback_data="retry"),
    ])
    keyboard.append([
        InlineKeyboardButton(t('buttons.cancel_import'), callback_data="cancel_import")
    ])

    return InlineKeyboardMarkup(keyboard)



def create_back_keyboard(idx):
    """Creates the keyboard with only the back button"""
    keyboard = [[InlineKeyboardButton(t('buttons.back'), callback_data=f"back_{idx}")]]
    return InlineKeyboardMarkup(keyboard)