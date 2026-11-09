"""
Pytest configuration and shared fixtures
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from unittest.mock import Mock, MagicMock


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mock Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def mock_config(monkeypatch):
    """Mock configuration values for testing"""
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test_token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '123456')
    monkeypatch.setenv('BEET_CONTAINER', 'test_beets')
    monkeypatch.setenv('BEET_USER', 'test_user')
    monkeypatch.setenv('IMPORT_PATH', '/test/imports')
    monkeypatch.setenv('LANGUAGE', 'en')
    monkeypatch.setenv('LOG_LEVEL', 'DEBUG')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Telegram Mocks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def mock_update():
    """Mock Telegram Update object"""
    update = Mock()
    update.message = Mock()
    update.message.chat_id = 123456
    update.message.text = "/start"
    update.message.reply_text = Mock()
    update.effective_chat = Mock()
    update.effective_chat.id = 123456
    return update


@pytest.fixture
def mock_context():
    """Mock Telegram Context object"""
    context = Mock()
    context.user_data = {}
    context.bot = Mock()
    context.bot.send_message = Mock()
    context.bot.edit_message_text = Mock()
    context.bot.edit_message_reply_markup = Mock()
    return context


@pytest.fixture
def mock_query():
    """Mock Telegram CallbackQuery object"""
    query = Mock()
    query.data = "test_callback"
    query.message = Mock()
    query.message.chat = Mock()
    query.message.chat.id = 123456
    query.message.message_id = 999
    query.message.reply_text = Mock()
    query.answer = Mock()
    query.edit_message_text = Mock()
    query.edit_message_reply_markup = Mock()
    return query


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Beet Manager Mocks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def mock_manager():
    """Mock BeetImportManager"""
    manager = Mock()
    manager.current_import = None
    manager.get_import_directories = Mock(return_value=[])
    manager.start_import = Mock()
    manager.import_with_id = Mock()
    manager.clear_state = Mock()
    manager.save_state = Mock()
    manager.load_state = Mock()
    return manager


@pytest.fixture
def sample_import_result():
    """Sample import result for testing"""
    return {
        'status': 'single_match',
        'path': '/test/Pink_Floyd-The_Wall',
        'has_multiple_candidates': False,
        'selected_index': None,
        'single_match': {
            'number': 1,
            'similarity': 95.5,
            'artist': 'Pink Floyd',
            'album': 'The Wall',
            'year': 1979,
            'mb_id': 'abc12345-1234-5678-9012-123456789abc',
            'mb_url': 'https://musicbrainz.org/release/abc12345-1234-5678-9012-123456789abc',
            'discogs_id': None,
            'discogs_url': None,
            'source': 'musicbrainz',
            'differences': [
                '≠ artist: Pink Floyd -> Pink Floyd, The',
                '≠ (#1) In the Flesh? (3:20) -> (#1) In the Flesh? (3:19)'
            ],
            'info_short': 'Pink Floyd - The Wall'
        },
        'candidates': [],
        'raw_output': 'Match (95.5%): Pink Floyd - The Wall',
        'timestamp': '2025-01-01T12:00:00Z'
    }


@pytest.fixture
def sample_candidates_result():
    """Sample multiple candidates result"""
    return {
        'status': 'has_candidates',
        'path': '/test/Unknown_Album',
        'has_multiple_candidates': True,
        'selected_index': None,
        'single_match': None,
        'candidates': [
            {
                'number': 1,
                'similarity': 85.0,
                'artist': 'Artist A',
                'album': 'Album A',
                'year': 2020,
                'mb_id': 'id-1',
                'mb_url': 'https://musicbrainz.org/release/id-1',
                'source': 'musicbrainz',
                'differences': [],
            },
            {
                'number': 2,
                'similarity': 82.5,
                'artist': 'Artist B',
                'album': 'Album B',
                'year': 2019,
                'discogs_id': 'r123456',
                'discogs_url': 'https://www.discogs.com/release/123456',
                'source': 'discogs',
                'differences': [],
            }
        ],
        'raw_output': 'Candidates: ...',
        'timestamp': '2025-01-01T12:00:00Z'
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Plugin Detector Mocks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def mock_plugin_detector(monkeypatch):
    """Mock plugin detector to avoid calling Docker"""
    mock_detector = Mock()
    mock_detector.get_enabled_plugins = Mock(return_value={'musicbrainz', 'discogs'})
    mock_detector.has_plugin = Mock(side_effect=lambda p: p in {'musicbrainz', 'discogs'})
    mock_detector.has_discogs = Mock(return_value=True)
    mock_detector.has_musicbrainz = Mock(return_value=True)
    mock_detector.get_metadata_sources = Mock(return_value=['musicbrainz', 'discogs'])
    
    # Patch the global detector
    monkeypatch.setattr('core.plugin_detector._detector', mock_detector)
    return mock_detector


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Filesystem Mocks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def mock_directory_structure(tmp_path):
    """Create a mock directory structure for testing"""
    # Create test directories
    album_dir = tmp_path / "Pink_Floyd-The_Wall"
    album_dir.mkdir()
    
    # Add some dummy files
    (album_dir / "01_track.flac").write_text("dummy audio")
    (album_dir / "02_track.flac").write_text("dummy audio")
    (album_dir / "cover.jpg").write_text("dummy image")
    
    # Multi-disc structure
    multi_disc = tmp_path / "Multi_Disc_Album"
    multi_disc.mkdir()
    cd1 = multi_disc / "CD1"
    cd1.mkdir()
    cd2 = multi_disc / "CD2"
    cd2.mkdir()
    
    (cd1 / "01_track.flac").write_text("dummy")
    (cd2 / "01_track.flac").write_text("dummy")
    (multi_disc / "cover.jpg").write_text("dummy")
    
    return tmp_path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pytest Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def pytest_configure(config):
    """Configure pytest"""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "requires_docker: marks tests that need Docker running"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection"""
    # Skip Docker tests if Docker not available
    skip_docker = pytest.mark.skip(reason="Docker not available")
    
    for item in items:
        if "requires_docker" in item.keywords:
            # Check if Docker is available
            try:
                import subprocess
                result = subprocess.run(
                    ["docker", "ps"], 
                    capture_output=True, 
                    timeout=5
                )
                if result.returncode != 0:
                    item.add_marker(skip_docker)
            except:
                item.add_marker(skip_docker)
