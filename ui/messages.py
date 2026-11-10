"""
Telegram message formatting (refactored for unified import model)
"""
from pathlib import Path
from config import setup_logging
from i18n.translations import t
from telegram.helpers import escape_markdown
from core.plugin_detector import has_discogs_plugin
from ui.match_view import format_candidates, format_match_body

logger = setup_logging()

# 🎯 SAFETY LIMITS: Prevent messages from becoming too long
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

    msg += format_import_warnings(structure)
    logger.debug("""format_directory_details:\n%s""", msg)
    return msg


def format_import_warnings(structure) -> str:
    """Problems beet would hit on import, shown before starting it."""
    parts = structure.get('discs') or [structure]
    unsupported = [f for part in parts for f in part.get('unsupported_audio', [])]
    cue_parts = [part for part in parts if part.get('cue_image')]

    warnings = []
    if unsupported:
        exts = ', '.join(sorted({f['ext'] for f in unsupported}))
        warnings.append(t('directory.unsupported_audio', count=len(unsupported),
                          exts=escape_markdown(exts, version=2)))
    if cue_parts:
        warnings.append(t('directory.cue_image'))
    return "\n" + "\n".join(warnings) + "\n" if warnings else ""


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
    if single:
        msg += format_match_body(single) + "\n"
        msg += _match_links(single)
        msg += "\n" + t('status.ask_confirm') + "\n"

    # --- Multiple Candidates ---
    elif result.get('candidates'):
        msg += format_candidates(result['candidates'], MAX_CANDIDATES_SHOWN) + "\n"

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