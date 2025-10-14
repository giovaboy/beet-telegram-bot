"""
Creation of inline keyboards for Telegram
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
    
    keyboard.append([InlineKeyboardButton(t('buttons.refresh'), callback_data="refresh_list")])
    
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
    
    # Delete
    keyboard.append([InlineKeyboardButton(t('buttons.delete'), callback_data=f"confirm_delete_{idx}")])
    
    # Back + Import
    keyboard.append([
        InlineKeyboardButton(t('buttons.back_to_list'), callback_data="refresh_list"),
        InlineKeyboardButton(t('buttons.start_import'), callback_data=f"start_import_{idx}")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def create_delete_confirm_keyboard(idx, dir_name):
    """Creates the deletion confirmation keyboard"""
    keyboard = [
        [InlineKeyboardButton(
            t('buttons.confirm_delete', name=dir_name), 
            callback_data=f"delete_final_{idx}"
        )],
        [InlineKeyboardButton(t('buttons.cancel'), callback_data=f"import_{idx}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_import_status_keyboard(result):
    """Creates the import status keyboard"""
    keyboard = []
    
    # Single Match with ID
    if result['status'] == 'single_match' and 'match_info' in result:
        info = result['match_info']
        if info.get('mb_id'):
            keyboard.append([
                InlineKeyboardButton(
                    t('buttons.accept_match', similarity=info.get('similarity', 'N/A')),
                    callback_data=f"selectid_{info['mb_id']}"
                )
            ])
    
    # Multiple Candidates
    elif result['status'] == 'has_candidates' and 'candidates' in result:
        for cand in result['candidates'][:5]:
            num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣'][int(cand['number']) - 1] if int(cand['number']) <= 5 else f"{cand['number']}."
            
            button_text = f"✅ ID: {cand['info'][:30]}..." if cand['mb_id'] else f"{num_emoji} {cand['info'][:30]}..."
            callback_data = f"selectid_{cand['mb_id']}" if cand['mb_id'] else f"select_{cand['number']}"
            
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # Generic buttons for input-required states
    if result['status'] in ['no_match', 'multiple_matches', 'unknown', 'waiting_input', 'needs_input', 'low_similarity', 'has_candidates', 'single_match']:
        keyboard.append([
            InlineKeyboardButton(t('buttons.mb_id'), callback_data="mb_id"),
            InlineKeyboardButton(t('buttons.discogs_id'), callback_data="discogs_id")
        ])
        
        keyboard.append([
            InlineKeyboardButton(t('buttons.import_as_is'), callback_data="as_is"),
            #InlineKeyboardButton(t('buttons.force_first'), callback_data="force_import")
        ])
        
        keyboard.append([
            InlineKeyboardButton(t('buttons.skip'), callback_data="skip"),
            InlineKeyboardButton(t('buttons.retry'), callback_data="retry"),
            InlineKeyboardButton(t('buttons.info'), callback_data="search_more")
        ])
        
        keyboard.append([
            InlineKeyboardButton(t('buttons.cancel_import'), callback_data="cancel_import")
        ])
    
    return InlineKeyboardMarkup(keyboard) if keyboard else None

def create_back_keyboard(idx):
    """Creates the keyboard with only the back button"""
    keyboard = [[InlineKeyboardButton(t('buttons.back'), callback_data=f"import_{idx}")]]
    return InlineKeyboardMarkup(keyboard)
