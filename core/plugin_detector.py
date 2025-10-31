"""
Plugin detection system for beet
Detects which plugins are enabled and adapts UI accordingly
"""
import subprocess
import json
import re
from pathlib import Path
from config import BEET_CONTAINER, BEET_USER, setup_logging

logger = setup_logging()


class BeetPluginDetector:
    """Detects and caches beet plugin configuration"""

    def __init__(self):
        self._cache = None
        self._cache_timestamp = 0
        self.CACHE_TTL = 300  # 5 minutes

    def _build_command(self, beet_args):
        """Build command for Docker or local execution"""
        if BEET_CONTAINER:
            cmd = ["docker", "exec"]
            if BEET_USER:
                cmd.extend(["-u", BEET_USER])
            cmd.extend([BEET_CONTAINER] + beet_args)
        else:
            cmd = beet_args
        return cmd

    def _run_beet_config(self):
        """Run 'beet config' and return output"""
        try:
            cmd = self._build_command(["beet", "config"])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(f"beet config failed: {result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            logger.error("beet config timed out")
            return None
        except Exception as e:
            logger.error(f"Error running beet config: {e}")
            return None

    def _parse_plugins_from_config(self, config_output):
        """
        Parse plugins from beet config output.

        Expected format:
        plugins: discogs musicbrainz fetchart lastgenre

        or YAML format:
        plugins:
          - discogs
          - musicbrainz
          - fetchart
        """
        if not config_output:
            return set()

        plugins = set()

        # Method 1: Single line format "plugins: plugin1 plugin2 plugin3"
        single_line_match = re.search(r'^plugins:\s*(.+)$', config_output, re.MULTILINE)
        if single_line_match:
            plugin_list = single_line_match.group(1).strip()
            # Split by whitespace or comma
            plugins.update(p.strip() for p in re.split(r'[\s,]+', plugin_list) if p.strip())

        # Method 2: Multi-line YAML format
        in_plugins_section = False
        for line in config_output.splitlines():
            stripped = line.strip()

            if stripped.startswith('plugins:'):
                in_plugins_section = True
                # Check if plugins are on same line
                after_colon = stripped.split(':', 1)[1].strip()
                if after_colon and not after_colon.startswith('['):
                    plugins.update(p.strip() for p in after_colon.split() if p.strip())
                continue

            if in_plugins_section:
                # Check for list item (starts with - or bullet)
                if stripped.startswith(('-', '•', '*')):
                    plugin_name = stripped.lstrip('-•* ').strip()
                    if plugin_name and not plugin_name.endswith(':'):
                        plugins.add(plugin_name)
                # Stop if we hit another section
                elif stripped and not stripped.startswith((' ', '\t', '-', '•', '*')):
                    in_plugins_section = False

        logger.info(f"Detected plugins: {plugins}")
        return plugins

    def get_enabled_plugins(self, force_refresh=False):
        """
        Get set of enabled plugins.
        Returns cached result if available and fresh.

        Returns:
            set: Set of enabled plugin names (e.g., {'discogs', 'musicbrainz', 'fetchart'})
        """
        import time
        now = time.time()

        # Return cached result if fresh
        if not force_refresh and self._cache and (now - self._cache_timestamp) < self.CACHE_TTL:
            return self._cache

        # Fetch fresh config
        config_output = self._run_beet_config()
        plugins = self._parse_plugins_from_config(config_output)

        # Update cache
        self._cache = plugins
        self._cache_timestamp = now

        return plugins

    def has_plugin(self, plugin_name):
        """Check if a specific plugin is enabled"""
        plugins = self.get_enabled_plugins()
        return plugin_name.lower() in {p.lower() for p in plugins}

    def has_discogs(self):
        """Check if discogs plugin is enabled"""
        return self.has_plugin('discogs')

    def has_musicbrainz(self):
        """Check if musicbrainz plugin is enabled (almost always true)"""
        return self.has_plugin('musicbrainz')

    def get_metadata_sources(self):
        """
        Get available metadata sources in priority order.

        Returns:
            list: ['musicbrainz', 'discogs'] or just ['musicbrainz']
        """
        sources = []

        # MusicBrainz is default and usually always enabled
        if self.has_musicbrainz() or True:  # Assume MB is always available
            sources.append('musicbrainz')

        if self.has_discogs():
            sources.append('discogs')

        return sources


# Global singleton instance
_detector = None

def get_plugin_detector():
    """Get the global plugin detector instance"""
    global _detector
    if _detector is None:
        _detector = BeetPluginDetector()
    return _detector


# Convenience functions
def has_discogs_plugin():
    """Quick check if Discogs is enabled"""
    return get_plugin_detector().has_discogs()

def has_musicbrainz_plugin():
    """Quick check if MusicBrainz is enabled"""
    return get_plugin_detector().has_musicbrainz()

def get_available_sources():
    """Get list of available metadata sources"""
    return get_plugin_detector().get_metadata_sources()
