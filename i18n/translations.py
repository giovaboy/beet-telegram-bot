"""
Internationalization system for the Beet Telegram Bot
"""
import json
import logging
from pathlib import Path
from config import LANGUAGE  # Assumi che LANGUAGE sia ora 'en'

logger = logging.getLogger(__name__)


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
            logger.warning(f"Translation file not found: {locale_file}, falling back to en.json")
            locale_file = Path(__file__).parent / 'locales' / 'en.json'
        
        try:
            with open(locale_file, 'r', encoding='utf-8') as f:
                self.translations = json.load(f)
            logger.info(f"Loaded translations from {locale_file}")
        except Exception as e:
            logger.error(f"Failed to load translations: {e}")
            self.translations = {}
    
    def t(self, key, **kwargs):
        """
        Translates a key with optional parameters.
        
        Args:
            key: Key in "section.subsection.key" format
            **kwargs: Parameters to substitute in the text
        
        Returns:
            Translated text with substituted parameters, or safe fallback if not found
        """
        keys = key.split('.')
        value = self.translations
        
        # Navigate the JSON structure
        for i, k in enumerate(keys):
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    # 🎯 KEY NOT FOUND - Log and return safe fallback
                    logger.warning(
                        f"Translation key not found: '{key}' at level '{'.'.join(keys[:i+1])}' (language: {self.language})"
                    )
                    return self._safe_fallback(key, **kwargs)
            else:
                # Invalid structure (value is not a dict but we're trying to navigate deeper)
                logger.warning(
                    f"Invalid translation structure for key: '{key}' at '{'.'.join(keys[:i])}' (language: {self.language})"
                )
                return self._safe_fallback(key, **kwargs)
        
        if value is None:
            # Shouldn't reach here, but safety check
            logger.warning(f"Translation key '{key}' resolved to None (language: {self.language})")
            return self._safe_fallback(key, **kwargs)
        
        # Value found - format and return
        if isinstance(value, str):
            # Substitute the parameters
            if kwargs:
                try:
                    return value.format(**kwargs)
                except KeyError as e:
                    # Missing parameter - log but try to continue
                    logger.warning(
                        f"Missing format parameter for key '{key}': {e}"
                    )
                    # Replace missing params with placeholder
                    safe_kwargs = {k: v for k, v in kwargs.items()}
                    try:
                        # Try with available params
                        return value.format(**safe_kwargs)
                    except:
                        # Still failing - use fallback
                        return self._safe_fallback(key, **kwargs)
                except Exception as e:
                    logger.error(f"Error formatting translation '{key}': {e}")
                    return self._safe_fallback(key, **kwargs)
            
            return value
        else:
            # Value is not a string (shouldn't happen with proper JSON)
            logger.warning(
                f"Translation key '{key}' is not a string: {type(value)} (language: {self.language})"
            )
            return self._safe_fallback(key, **kwargs)
    
    def _safe_fallback(self, key: str, **kwargs) -> str:
        """
        Generate a safe fallback string for missing translation keys.
        
        Removes MarkdownV2 special characters to prevent parsing errors.
        
        Args:
            key: The missing translation key
            **kwargs: Format parameters (will be included in fallback)
        
        Returns:
            Safe fallback string without MarkdownV2 special chars
        """
        # Start with the key itself (last part after dot)
        parts = key.split('.')
        fallback = parts[-1] if parts else key
        
        # Replace underscores with spaces and title case
        fallback = fallback.replace('_', ' ').title()
        
        # Add any kwargs values (safely)
        if kwargs:
            # Format kwargs safely, avoiding special chars
            params_list = []
            for k, v in kwargs.items():
                # Convert value to string and sanitize
                v_str = str(v)
                v_safe = self._sanitize_markdown(v_str)
                params_list.append(f"{k}={v_safe}")
            
            params = ', '.join(params_list)
            fallback = f"{fallback} ({params})"
        
        # 🎯 CRITICAL: Remove MarkdownV2 special characters
        fallback = self._sanitize_markdown(fallback)
        
        # Add warning prefix (emoji doesn't need escaping)
        fallback = f"⚠️ {fallback}"
        
        return fallback
    
    @staticmethod
    def _sanitize_markdown(text: str) -> str:
        """
        Remove MarkdownV2 special characters from text.
        
        These chars need escaping in MarkdownV2: _ * [ ] ( ) ~ ` > # + - = | { } . !
        Instead of escaping, we remove them to ensure safety.
        
        Args:
            text: Input text that may contain special characters
        
        Returns:
            Sanitized text safe for MarkdownV2
        """
        # List of MarkdownV2 special characters to remove
        markdown_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', 
                         '#', '+', '-', '=', '|', '{', '}', '.', '!']
        
        result = text
        for char in markdown_chars:
            result = result.replace(char, '')
        
        # Clean up multiple spaces
        while '  ' in result:
            result = result.replace('  ', ' ')
        
        return result.strip()


# Global instance of the translator
_translator = Translator(LANGUAGE)


def t(key, **kwargs):
    """
    Helper function for translation.
    
    Usage:
        t('directory.metadata', count=5)
        → "📄 5 metadata files"
        
        t('missing.key', count=5)  # Key not found
        → "⚠️ Key (count=5)"
        → Logs: "WARNING - Translation key not found: 'missing.key'"
    
    Args:
        key: Translation key in dot notation
        **kwargs: Format parameters
    
    Returns:
        Translated string or safe fallback
    """
    return _translator.t(key, **kwargs)


def get_translator():
    """Gets the translator instance"""
    return _translator


# Example usage and testing
if __name__ == '__main__':
    # Setup logging for testing
    logging.basicConfig(level=logging.DEBUG)
    
    # Test cases
    print("=== Translation System Tests ===\n")
    
    # Test 1: Normal translation
    print("1. Normal key:")
    print(f"   t('directory.metadata', count=5)")
    print(f"   → {t('directory.metadata', count=5)}\n")
    
    # Test 2: Missing key
    print("2. Missing key:")
    print(f"   t('fake.missing.key', count=5)")
    print(f"   → {t('fake.missing.key', count=5)}\n")
    
    # Test 3: Key with special chars
    print("3. Missing key with special chars:")
    print(f"   t('test.special_chars!', value='test_value')")
    print(f"   → {t('test.special_chars!', value='test_value')}\n")
    
    # Test 4: Missing parameter
    print("4. Missing format parameter:")
    print(f"   t('directory.metadata')  # Missing 'count'")
    print(f"   → {t('directory.metadata')}\n")