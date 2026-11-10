"""
Centralized configuration for the Beet Telegram Bot
"""
import os
import json
import logging

# Telegram Configuration
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Beet Configuration
BEET_CONTAINER = os.environ.get('BEET_CONTAINER')
BEET_USER = os.environ.get('BEET_USER')
BEET_LIBRARY = os.environ.get('BEET_LIBRARY', '/music')
IMPORT_PATH = os.environ.get('IMPORT_PATH', '/downloads')
BEET_DEBUG_MODE = os.environ.get('BEET_DEBUG_MODE', 'false').lower() == 'true'
BEET_PRETEND = os.environ.get('BEET_PRETEND','false').lower() == 'true'

# Custom beet container commands
CUSTOM_COMMANDS_JSON = os.environ.get('CUSTOM_COMMANDS', '[]')
CUSTOM_COMMANDS = json.loads(CUSTOM_COMMANDS_JSON)

# Internationalization Configuration
LANGUAGE = os.environ.get('LANGUAGE', 'en')

# State file
STATE_FILE = '/tmp/beet_import_state.json'
# Per-chat bot state (active panel, directory list snapshot...), survives restarts
PERSISTENCE_FILE = os.path.join(os.path.dirname(STATE_FILE), 'beet_bot_persistence.pickle')

# Supported file extensions
# Formats beet can import (beets' mediafile: MP3, AAC/ALAC, OGG, Opus, FLAC,
# APE, WavPack, Musepack, WMA, AIFF, DSF, WAV)
AUDIO_EXTENSIONS = {
    '.flac', '.mp3', '.m4a', '.alac', '.ogg', '.oga', '.opus', '.wav', '.wv', '.ape',
    '.mpc', '.wma', '.aif', '.aiff', '.aifc', '.dsf',
}
# Audio that beet cannot import: shown as a warning instead of being ignored
UNSUPPORTED_AUDIO_EXTENSIONS = {
    '.dff', '.tak', '.mka', '.shn', '.tta', '.ofr', '.aac', '.ac3', '.dts', '.iso',
}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
METADATA_EXTENSIONS = {'.pdf', '.nfo', '.txt', '.log', '.cue', '.m3u'}

# Logging Configuration
LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')


def parse_log_level(value):
    """
    'debug', ' Info ', 'WARN' → canonical level name.
    Returns (level, invalid_value): unknown values fall back to INFO.
    """
    name = (value or 'INFO').strip().upper()
    name = {'WARN': 'WARNING', 'FATAL': 'CRITICAL'}.get(name, name)
    if name in LOG_LEVELS:
        return name, None
    return 'INFO', value


LOG_LEVEL, _INVALID_LOG_LEVEL = parse_log_level(os.environ.get('LOG_LEVEL'))
_log_level_warned = False


def setup_logging():
    """Configures logging for the application"""
    global _log_level_warned
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Reduce verbosity of telegram
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    if _INVALID_LOG_LEVEL is not None and not _log_level_warned:
        _log_level_warned = True
        logger.warning(f"Invalid LOG_LEVEL={_INVALID_LOG_LEVEL!r}, using INFO "
                       f"(valid: {', '.join(LOG_LEVELS)})")
    return logger
