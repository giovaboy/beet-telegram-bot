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

# Custom beet container commands
CUSTOM_COMMANDS_JSON = os.environ.get('CUSTOM_COMMANDS', '[]')
CUSTOM_COMMANDS = json.loads(CUSTOM_COMMANDS_JSON)

# Internationalization Configuration
LANGUAGE = os.environ.get('LANGUAGE', 'en')

# State file
STATE_FILE = '/tmp/beet_import_state.json'

# Supported file extensions
AUDIO_EXTENSIONS = {'.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.wv', '.ape'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

# Diff style for displaying differences
# Options: 'char', 'word', 'smart', 'simple'
DIFF_STYLE = os.environ.get('DIFF_STYLE', 'word')

# Logging Configuration
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'DEBUG')

def setup_logging():
    """Configures logging for the application"""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Reduce verbosity of telegram
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    return logging.getLogger(__name__)
