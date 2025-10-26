"""
Creation of inline keyboards for Telegram (refactored for new unified import model)
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from i18n.translations import t


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

    # Search buttons
    mb_url = f"https://musicbrainz.org/search?query={search_query}&type=release&method=indexed"
    discogs_url = f"https://www.discogs.com/search/?q={search_query}&type=release"

    keyboard.append([
        InlineKeyboardButton(t('buttons.search_mb'), url=mb_url),
        InlineKeyboardButton(t('buttons.search_discogs'), url=discogs_url)
    ])

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
    Creates keyboard for import results — unified for both single match and multiple candidates.
    """
    keyboard = []

    # --- Case: Single Match ---------------------------------------------------
    if result.get('single_match'):
        sm = result['single_match']
        similarity = sm.get('similarity', '?')
        keyboard.append([
            InlineKeyboardButton(
                t('buttons.accept_match', similarity=similarity),
                callback_data="single_match_accept"
            )
        ])

    # --- Case: Multiple Candidates --------------------------------------------
    elif result.get('candidates'):
        candidates = result['candidates'][:5]
        num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣']

        for i, cand in enumerate(candidates):
            raw_similarity = cand.get('similarity')
            raw_artist = cand.get('artist')
            raw_album = cand.get('album')
            raw_year = cand.get('year')
            
            # 2. Assegnazione del fallback se il valore è None
            # Usiamo '?' come fallback se il parser ha restituito null per il campo
            similarity = raw_similarity if raw_similarity is not None else '?'
            artist = raw_artist if raw_artist is not None else '?'
            album = raw_album if raw_album is not None else '?'
            year = raw_year if raw_year is not None else '?'
            
            label = f"{artist} — {album} ({year})"

            emoji = num_emoji[i] if i < len(num_emoji) else str(i + 1)
            keyboard.append([
                InlineKeyboardButton(f"{emoji} {label} [{similarity}%]", callback_data=f"match_{i}")
            ])

    # --- Case: Manual Input or Misc ------------------------------------------
    if not keyboard or result.get("status") in (
        "no_match", "needs_input", "unknown", "has_candidates", "single_match"
    ):
        keyboard.append([
            InlineKeyboardButton(t('buttons.mb_id'), callback_data="mb_id"),
            InlineKeyboardButton(t('buttons.discogs_id'), callback_data="discogs_id")
        ])
        keyboard.append([InlineKeyboardButton(t('buttons.import_as_is'), callback_data="as_is")])
        keyboard.append([
            InlineKeyboardButton(t('buttons.skip'), callback_data="skip"),
            InlineKeyboardButton(t('buttons.retry'), callback_data="retry"),
            InlineKeyboardButton(t('buttons.info'), callback_data="info")
        ])
        keyboard.append([InlineKeyboardButton(t('buttons.cancel_import'), callback_data="cancel_import")])

    return InlineKeyboardMarkup(keyboard)


def create_back_keyboard(idx):
    """Creates the keyboard with only the back button"""
    keyboard = [[InlineKeyboardButton(t('buttons.back'), callback_data=f"back_{idx}")]]
    return InlineKeyboardMarkup(keyboard)