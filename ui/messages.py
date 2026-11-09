"""
Telegram message formatting (refactored for unified import model)
"""
from pathlib import Path
from core.parsers import format_difference_with_diff
from config import setup_logging, DIFF_STYLE
from i18n.translations import t
from telegram.helpers import escape_markdown
from core.plugin_detector import has_discogs_plugin
from ui.match_view import format_match_body

logger = setup_logging()

# 🎯 SAFETY LIMITS: Prevent messages from becoming too long
MAX_DIFFERENCES_SHOWN = 30
MAX_CANDIDATES_SHOWN = 10
MAX_OUTPUT_LINES = 15


# Replace the format_directory_details function in ui/messages.py

def format_directory_details(dir_name, structure, parent_path=None):
    """
    Formats the directory details message.
    
    Args:
        dir_name: Album directory name
        structure: Analyzed structure dict
        parent_path: Optional parent path for nested albums (e.g., "Jazz Collection")
    """
    dir_name_escaped = escape_markdown(dir_name, version=2)
    
    # Show parent path if nested
    if parent_path:
        parent_escaped = escape_markdown(parent_path, version=2)
        msg = f"📂 *{parent_escaped}*\n📁 *{dir_name_escaped}*\n\n"
    else:
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
            
            # 🎯 NEW: Show metadata count per disc
            if disc.get('metadata_files'):
                msg += "  " + t('directory.metadata_files', count=len(disc['metadata_files'])) + "\n"
        
        # Root level images and metadata
        if structure['images']:
            msg += "\n" + t('directory.images_main', count=len(structure['images'])) + "\n"
        
        # 🎯 NEW: Root level metadata
        if structure.get('metadata_files'):
            msg += t('directory.metadata_main', count=len(structure['metadata_files'])) + "\n"
    
    else:
        msg += t('directory.single_album') + "\n\n"
        msg += t('directory.tracks',
            count=structure['audio_count'],
            size=int(structure['total_size'] / 1024 / 1024)
        ) + "\n"
        
        if structure['images']:
            msg += t('directory.images', count=len(structure['images'])) + "\n"
        
        # 🎯 NEW: Show metadata count for single albums
        if structure.get('metadata_files'):
            msg += t('directory.metadata_files', count=len(structure['metadata_files'])) + "\n"

    logger.debug("""format_directory_details:\n%s""", msg)
    return msg


def format_file_list(structure):
    """
    Formats the file list message - WITH LIMITS
    """
    # 🎯 ADAPTIVE LIMIT: Fewer files for multi-disc
    max_audio_files = 50 if structure['type'] == 'single_album' else 40

    msg = t('directory.file_list') + "\n\n"

    if structure['type'] == 'multi_disc':
        for disc in structure['discs']:
            disc_name_escaped = escape_markdown(disc['name'], version=2)
            msg += f"*{disc_name_escaped}*\n"

            # 🎯 LIMIT: Truncate file list
            files_shown = disc['audio_files'][:max_audio_files]

            for f in files_shown:
                size_mb = f['size'] / 1024 / 1024
                file_info = f"• {f['name'][:80]} ({size_mb:.1f} MB)"
                msg += f"  {escape_markdown(file_info, version=2)}\n"

            if len(disc['audio_files']) > max_audio_files:
                remaining = len(disc['audio_files']) - max_audio_files
                msg += "  " + t('directory.more_files', count=remaining) + "\n"

             # 🎯 Metadata files in disc
            if disc.get('metadata_files'):
                msg += "\n"
                for f in disc['metadata_files'][:10]:  # Max 10 metadata per disc
                    size_kb = f['size'] / 1024
                    icon = get_file_icon(f['ext'])
                    file_info = f"{icon} {f['name'][:70]} ({size_kb:.1f} KB)"
                    msg += f"  {escape_markdown(file_info, version=2)}\n"

            msg += "\n"

        # 🎯 Root level metadata
        if structure.get('metadata_files'):
            msg += "*📄 Root Metadata*\n"
            for f in structure['metadata_files'][:10]:
                size_kb = f['size'] / 1024
                icon = get_file_icon(f['ext'])
                file_info = f"{icon} {f['name'][:70]} ({size_kb:.1f} KB)"
                msg += f"{escape_markdown(file_info, version=2)}\n"
            msg += "\n"

    else:
        files_shown = structure['audio_files'][:max_audio_files]

        for f in files_shown:
            size_mb = f['size'] / 1024 / 1024
            file_info = f"• {f['name'][:80]} ({size_mb:.1f} MB)"
            msg += f"{escape_markdown(file_info, version=2)}\n"

        if len(structure['audio_files']) > max_audio_files:
            remaining = len(structure['audio_files']) - max_audio_files
            msg += "\n" + t('directory.more_files', count=remaining) + "\n"

        # 🎯 Metadata files
        if structure.get('metadata_files'):
            msg += "\n*📄 Metadata Files*\n"
            for f in structure['metadata_files'][:15]:  # Max 15 metadata files
                size_kb = f['size'] / 1024
                icon = get_file_icon(f['ext'])
                file_info = f"{icon} {f['name'][:70]} ({size_kb:.1f} KB)"
                msg += f"{escape_markdown(file_info, version=2)}\n"

            if len(structure.get('metadata_files', [])) > 15:
                remaining = len(structure['metadata_files']) - 15
                msg += "\n" + t('directory.more_metadata', count=remaining) + "\n"

    logger.debug(f"format_file_list: Generated message of {len(msg)} characters")
    return msg


def get_file_icon(ext: str) -> str:
    """
    Get appropriate icon for file extension.

    Args:
        ext: File extension (e.g., '.pdf', '.nfo')

    Returns:
        Emoji icon for the file type
    """
    icon_map = {
        '.pdf': '📕',
        '.nfo': '📋',
        '.txt': '📄',
        '.log': '📝',
        '.cue': '💿',
        '.m3u': '🎵',
        '.m3u8': '🎵',
    }

    return icon_map.get(ext.lower(), '📄')


def _match_links(single) -> str:
    """Links to the matched release (MusicBrainz / Discogs)."""
    links = ""
    if single.get('mb_url'):
        links += "\n" + t('fields.mb_link', url=single['mb_url']) + "\n"
    if single.get('discogs_url') and has_discogs_plugin():
        links += "\n" + t('fields.discogs_link', url=single['discogs_url']) + "\n"
    return links


def format_import_status(result):
    """
    Formats the import status message based on unified structure.
    NOW WITH PREVENTIVE LIMITS to avoid "message too long" errors.
    """

    # Escape name
    raw_path_name = result.get('path', '?')
    path_name = escape_markdown(Path(raw_path_name).name, version=2)

    emoji_map = {
        'success': '✅', 'error': '❌', 'no_match': '🔍',
        'has_candidates': '📋', 'single_match': '🎯',
        'needs_input': '⏸️', 'low_similarity': '⚠️'
    }
    emoji = emoji_map.get(result.get('status'), '📌')

    msg = f"{emoji} {t('status.header')}\n\n"
    msg += t('fields.directory', name=path_name) + "\n\n"

    # --- Single Match ---
    single = result.get('single_match')
    body = format_match_body(single) if single else None
    if single and body:
        msg += body + "\n"
        msg += _match_links(single)
        msg += "\n" + t('status.ask_confirm') + "\n"

    elif single:
        # Legacy rendering, used when beet's output could not be parsed in detail
        # Get values with None handling
        raw_similarity = single.get('similarity')
        raw_artist = single.get('artist')
        raw_album = single.get('album')
        raw_year = single.get('year')

        similarity_display = raw_similarity if raw_similarity is not None else '?'
        artist_display = raw_artist if raw_artist is not None else '?'
        album_display = raw_album if raw_album is not None else '?'
        year_display = raw_year if raw_year is not None else '?'

        single_similarity = escape_markdown(str(similarity_display), version=2)
        single_artist = escape_markdown(str(artist_display), version=2)
        single_album = escape_markdown(str(album_display), version=2)
        single_year = escape_markdown(str(year_display), version=2)

        msg += f"🎯 *{t('status.single_match', similarity=single_similarity)}*\n"
        msg += f"🎤 {t('fields.artist', artist=single_artist)}\n"
        msg += f"💿 {t('fields.album', album=single_album)}\n"
        msg += f"📅 {t('fields.year', year=single_year)}\n"

        if single.get('differences'):
            msg += "\n⚠️ *" + t('status.differences') + "*\n"

            # 🎯 LIMIT: Show only first N differences
            differences = single['differences'][:MAX_DIFFERENCES_SHOWN]

            for diff in differences:
                formatted = format_difference_with_diff(diff, style=DIFF_STYLE)
                msg += formatted + "\n"

            # Show count if truncated
            total_diffs = len(single.get('differences', []))
            if total_diffs > MAX_DIFFERENCES_SHOWN:
                remaining = total_diffs - MAX_DIFFERENCES_SHOWN
                msg += f"\n{t('fields.more_diff', remaining=remaining)}\n"

        msg += _match_links(single)
        msg += "\n" + t('status.ask_confirm') + "\n"

    # --- Multiple Candidates ---
    elif result.get('candidates'):
        cands = result['candidates']

        # 🎯 LIMIT: Show only first N candidates
        shown_cands = cands[:MAX_CANDIDATES_SHOWN]
        total_cands = len(cands)

        msg += f"📋 *{t('status.multiple_candidates', count=total_cands)}*\n\n"

        for i, c in enumerate(shown_cands):
            num = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣','8️⃣','9️⃣','🔟'][i] if i < 10 else f"{i + 1}\\."

            # None handling
            raw_similarity = c.get('similarity')
            raw_artist = c.get('artist')
            raw_album = c.get('album')
            raw_year = c.get('year')

            similarity_display = raw_similarity if raw_similarity is not None else '?'
            artist_display = raw_artist if raw_artist is not None else '?'
            album_display = raw_album if raw_album is not None else '?'
            year_display = raw_year if raw_year is not None else '?'

            artist = escape_markdown(str(artist_display), version=2)
            album = escape_markdown(str(album_display), version=2)
            year = escape_markdown(str(year_display), version=2)
            sim = escape_markdown(str(similarity_display), version=2)

            msg += f"{num} \\({sim}%\\) {artist} — _{album}_ \\({year}\\)\n\n"

        # Show count if truncated
        if total_cands > MAX_CANDIDATES_SHOWN:
            remaining = total_cands - MAX_CANDIDATES_SHOWN
            msg += f"\n_" + t('status.more_candidates', count=remaining) + "_\n"

    # --- Fallback / No match ---
    else:
        status_text = escape_markdown(result.get('status', '?'), version=2)
        msg += f"{t('fields.status', status=status_text)}\n"

        if 'output' in result:
            # 🎯 LIMIT: Show only first N lines
            output_lines = result['output'].splitlines()
            shown_lines = output_lines[:MAX_OUTPUT_LINES]

            lines = [escape_markdown(line, version=2) for line in shown_lines]
            msg += "\n```\n" + "\n".join(lines) + "\n```"

            if len(output_lines) > MAX_OUTPUT_LINES:
                msg += "\n_" + t('status.output_truncated') + "_"

    logger.debug(f"format_import_status: Generated message of {len(msg)} characters")

    # 🎯 FINAL CHECK: Log if still potentially too long
    if len(msg) > 3500:
        logger.warning(f"Message is {len(msg)} chars - close to Telegram limit!")

    return msg