"""
Telegram message formatting (refactored for unified import model)
"""
from pathlib import Path
from core.parsers import format_difference_with_diff
from config import setup_logging, DIFF_STYLE
from i18n.translations import t
from telegram.helpers import escape_markdown
from core.plugin_detector import has_discogs_plugin

logger = setup_logging()


def format_directory_details(dir_name, structure):
    """Formats the directory details message"""

    dir_name_escaped = escape_markdown(dir_name, version=2)
    msg = f"📁 *{dir_name_escaped}*\n\n"

    if structure['type'] == 'multi_disc':
        msg += t('directory.multi_disc', count=len(structure['discs'])) + "\n\n"
        for disc in structure['discs']:
            disc_name_escaped = escape_markdown(disc['name'], version=2)
            msg += f"*{disc_name_escaped}*\n"
            msg += "  " + t('directory.tracks',
                count=disc['audio_count'],
                size=int(disc['total_size'] / 1024 / 1024)
            ) + "\n"
            if disc['images']:
                msg += "  " + t('directory.images', count=len(disc['images'])) + "\n"
        if structure['images']:
            msg += "\n" + t('directory.images_main', count=len(structure['images'])) + "\n"
    else:
        msg += t('directory.single_album') + "\n\n"
        msg += t('directory.tracks',
            count=structure['audio_count'],
            size=int(structure['total_size'] / 1024 / 1024)
        ) + "\n"
        if structure['images']:
            msg += t('directory.images', count=len(structure['images'])) + "\n"

    logger.debug("""format_directory_details:\n%s""", msg)
    return msg


def format_file_list(structure):
    """Formats the file list message"""
    max_audio_files = 80 if structure['type'] == 'single_album' else 80
    msg = t('directory.file_list') + "\n\n"

    if structure['type'] == 'multi_disc':
        for disc in structure['discs']:
            disc_name_escaped = escape_markdown(disc['name'], version=2)
            msg += f"*{disc_name_escaped}*\n"
            for f in disc['audio_files'][:max_audio_files]:
                size_mb = f['size'] / 1024 / 1024
                file_info = f"• {f['name'][:80]} ({size_mb:.1f} MB)"
                msg += f"  {escape_markdown(file_info, version=2)}\n"
            if len(disc['audio_files']) > max_audio_files:
                msg += "  " + t('directory.more_files', count=len(disc['audio_files']) - max_audio_files) + "\n"
            msg += "\n"
    else:
        for f in structure['audio_files'][:max_audio_files]:
            size_mb = f['size'] / 1024 / 1024
            file_info = f"• {f['name'][:80]} ({size_mb:.1f} MB)"
            msg += f"{escape_markdown(file_info, version=2)}\n"
        if len(structure['audio_files']) > max_audio_files:
            msg += "\n" + t('directory.more_files', count=len(structure['audio_files']) - max_audio_files) + "\n"

    logger.debug("""format_file_list:\n%s""", msg)
    return msg


def format_import_status(result):
    """Formats the import status message based on unified structure"""

    # *** ESCAPE IMMEDIATO PER IL NOME DELLA CARTELLA ***
    raw_path_name = result.get('path', '?')
    path_name = escape_markdown(Path(raw_path_name).name, version=2) # Escapiamo il nome del file/cartella

    emoji_map = {
        'success': '✅', 'error': '❌', 'no_match': '🔍',
        'has_candidates': '📋', 'single_match': '🎯',
        'needs_input': '⏸️', 'low_similarity': '⚠️'
    }
    emoji = emoji_map.get(result.get('status'), '📝')

    msg = f"{emoji} *{t('status.header')}*\n\n"

    # Usa il nome escapato
    msg += t('fields.directory', name=path_name) + "\n\n"

    # --- Single Match ---
    single = result.get('single_match')
    if single:
        # 1. Recupero e gestione di None (se il valore è 'null' nel JSON)
        raw_similarity = single.get('similarity')
        raw_artist = single.get('artist')
        raw_album = single.get('album')
        raw_year = single.get('year')

        # 2. Assegnazione del fallback se il valore è None
        # Usiamo '?' come fallback se il parser ha restituito null per il campo
        similarity_display = raw_similarity if raw_similarity is not None else '?'
        artist_display = raw_artist if raw_artist is not None else '?'
        album_display = raw_album if raw_album is not None else '?'
        year_display = raw_year if raw_year is not None else '?'

        # 3. Escape e conversione finale a stringa
        single_similarity = escape_markdown(str(similarity_display), version=2)
        single_artist = escape_markdown(str(artist_display), version=2)
        single_album = escape_markdown(str(album_display), version=2)
        single_year = escape_markdown(str(year_display), version=2)

        msg += f"🎯 *{t('status.single_match', similarity=single_similarity)}*\n"

        # Usa le variabili sanificate ed escapate
        msg += f"🎤 {t('fields.artist', artist=single_artist)}\n"
        msg += f"💿 {t('fields.album', album=single_album)}\n"
        msg += f"📅 {t('fields.year', year=single_year)}\n"

        if single.get('differences'):
            msg += "\n⚠️ *" + t('status.differences') + "*\n"

            # 🎯 ENHANCED: Use character-level diff highlighting
            for diff in single['differences'][:100]:
                # Style options: 'char' | 'word' | 'smart' | 'simple'
                formatted = format_difference_with_diff(diff, style=DIFF_STYLE)
                msg += formatted + "\n"

            if len(single['differences']) > 100:
                remaining = len(single['differences']) - 100
                msg += f"\n{t('fields.more_diff',remaining=remaining)}\n"

        if single.get('mb_url'):
            # Il link va bene, Telegram gestisce l'escape della URL
            msg += "\n" + t('fields.mb_link', url=single['mb_url']) + "\n"

        if single.get('discogs_url') and has_discogs_plugin():
            msg += "\n" + t('fields.discogs_link', url=single['discogs_url']) + "\n"

        msg += "\n" + t('status.ask_confirm') + "\n"

    # --- Multiple Candidates ---
    elif result.get('candidates'):
        cands = result['candidates']
        msg += f"📋 *{t('status.multiple_candidates', count=len(cands))}*\n\n"
        for i, c in enumerate(cands[:10]):
            num = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣','8️⃣','9️⃣','🔟'][i] if i < 10 else f"{i + 1}\."

            # 1. Recupero e gestione di None (se il valore è 'null' nel JSON)
            raw_similarity = c.get('similarity')
            raw_artist = c.get('artist')
            raw_album = c.get('album')
            raw_year = c.get('year')

            # 2. Assegnazione del fallback se il valore è None
            # Usiamo '?' come fallback se il parser ha restituito null per il campo
            similarity_display = raw_similarity if raw_similarity is not None else '?'
            artist_display = raw_artist if raw_artist is not None else '?'
            album_display = raw_album if raw_album is not None else '?'
            year_display = raw_year if raw_year is not None else '?'

            # ESCAPE di tutte le variabili dinamiche nel candidato
            artist = escape_markdown(artist_display, version=2)
            album = escape_markdown(album_display, version=2)
            year = escape_markdown(str(year_display), version=2)
            sim = escape_markdown(str(similarity_display), version=2)

            # Nota: '—' (em dash) non è riservato, ma il '-' (hyphen) sì.
            # Qui usiamo l'em dash, quindi non lo escapiamo.
            msg += f"{num} \({sim}%\) {artist} — _{album}_ \({year}\)\n\n"
        if len(cands) > 10:
            msg += f"\n_" + t('status.more_candidates', count=len(cands) - 10) + "_\n"

    # --- Fallback / No match ---
    else:
        # Escape per lo status e l'output
        status_text = escape_markdown(result.get('status', '?'), version=2)
        msg += f"{t('fields.status', status=status_text)}\n"

        if 'output' in result:
            # Escapiamo solo le righe di output per i caratteri riservati
            # (es. se l'output contiene un'intestazione con # o un path con .)
            lines = [escape_markdown(line, version=2) for line in result['output'].splitlines()[:10]]

            # Per il codice block, togliamo l'escape del backtick (`) se Python lo permette,
            # ma l'API di Telegram è più sicura se escapiamo prima di mettere nel blocco,
            # quindi teniamo l'escape per sicurezza
            msg += "\n```\n" + "\n".join(lines) + "\n```"

            if len(result['output'].splitlines()) > 10:
                msg += "\n_" + t('status.output_truncated') + "_"

    logger.debug("""format_import_status:\n%s""", msg)
    return msg