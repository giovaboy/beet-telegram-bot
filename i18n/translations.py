"""
Internationalization system for the Beet Telegram Bot
"""
import json
from pathlib import Path
from config import LANGUAGE # Assumi che LANGUAGE sia ora 'en'

class Translator:
    def __init__(self, language='en'):
        self.language = language
        self.translations = {}
        self.load_translations()
    
    def load_translations(self):
        """Loads translations from the JSON file"""
        # Attempts to load the file for the configured language (e.g., 'en.json')
        locale_file = Path(__file__).parent / 'locales' / f'{self.language}.json'
        
        if not locale_file.exists():
            # Fallback to English if the language file does not exist
            # NOTE: Assuming the primary language is now English (en.json)
            locale_file = Path(__file__).parent / 'locales' / 'en.json'
        
        with open(locale_file, 'r', encoding='utf-8') as f:
            self.translations = json.load(f)
    
    def t(self, key, **kwargs):
        """
        Translates a key with optional parameters
        
        Args:
            key: Key in "section.subsection.key" format
            **kwargs: Parameters to substitute in the text
        
        Returns:
            Translated text with substituted parameters
        """
        keys = key.split('.')
        value = self.translations
        
        # Navigate the JSON structure
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return key  # Fallback to the key if not found
        
        if value is None:
            return key  # Fallback to the key if not found
        
        # Substitute the parameters
        if kwargs:
            try:
                return value.format(**kwargs)
            except KeyError:
                return value
        
        return value

# Global instance of the translator
_translator = Translator(LANGUAGE)

def t(key, **kwargs):
    """Helper function for translation"""
    return _translator.t(key, **kwargs)

def get_translator():
    """Gets the translator instance"""
    return _translator
