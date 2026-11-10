#!/usr/bin/env python3
"""
Test suite for beet output parsers
Tests parsing of real beet output from fixtures
"""
import pytest
from pathlib import Path
from core.parsers import (
    parse_beet_output,
    parse_beet_match_info,
    parse_verbose_candidates,
    clean_ansi_codes,
    extract_musicbrainz_id,
    extract_discogs_id,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fixtures
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def fixtures_dir():
    """Return path to fixtures directory"""
    return Path(__file__).parent / "fixtures"


def load_fixture(fixtures_dir, filename):
    """Load a fixture file and return its content"""
    filepath = fixtures_dir / filename
    if not filepath.exists():
        pytest.skip(f"Fixture not found: {filename}")
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Parse metadata if present
    metadata = {}
    if content.startswith('#'):
        lines = content.split('\n')
        for line in lines:
            if line.startswith('# ---'):
                break
            if line.startswith('#') and ':' in line:
                key, value = line[1:].split(':', 1)
                metadata[key.strip().lower()] = value.strip()
        
        # Remove metadata from content
        content = content.split('# ---\n', 1)[1] if '# ---' in content else content
    
    return content, metadata


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: ANSI Code Cleaning
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_clean_ansi_codes():
    """Test ANSI escape code removal"""
    # With ANSI codes
    input_text = "\x1B[32mGreen text\x1B[0m and \x1B[1;31mBold red\x1B[0m"
    expected = "Green text and Bold red"
    
    result = clean_ansi_codes(input_text)
    assert result == expected


def test_clean_ansi_codes_empty():
    """Test with empty string"""
    assert clean_ansi_codes("") == ""
    assert clean_ansi_codes(None) == ""


def test_clean_ansi_codes_no_codes():
    """Test with plain text"""
    plain = "Just plain text"
    assert clean_ansi_codes(plain) == plain


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: ID Extraction
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_extract_musicbrainz_id():
    """Test MusicBrainz UUID extraction"""
    text = "Release: https://musicbrainz.org/release/abc12345-1234-5678-9012-123456789abc"
    result = extract_musicbrainz_id(text)
    assert result == "abc12345-1234-5678-9012-123456789abc"


def test_extract_musicbrainz_id_not_found():
    """Test when no MB ID present"""
    text = "No MusicBrainz ID here"
    result = extract_musicbrainz_id(text)
    assert result is None


def test_extract_discogs_id_with_prefix():
    """Test Discogs ID with r/m prefix"""
    assert extract_discogs_id("Release r123456") == "r123456"
    assert extract_discogs_id("Master m789012") == "m789012"


def test_extract_discogs_id_from_url():
    """Test Discogs ID from URL"""
    text = "https://www.discogs.com/release/123456"
    result = extract_discogs_id(text)
    assert result == "r123456"


def test_extract_discogs_id_not_found():
    """Test when no Discogs ID present"""
    text = "No Discogs ID here"
    result = extract_discogs_id(text)
    assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Single Match Parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_parse_single_match_musicbrainz(fixtures_dir):
    """Test parsing single MusicBrainz match from fixture"""
    try:
        content, metadata = load_fixture(fixtures_dir, "single_match_mb.txt")
    except:
        pytest.skip("Fixture single_match_mb.txt not available")
        return
    
    info = parse_beet_match_info(content)
    
    # Verify basic fields
    assert info['similarity'] is not None
    assert info['similarity'] > 0
    assert info['artist'] is not None
    assert info['album'] is not None
    
    # Verify MusicBrainz specific
    assert info['mb_id'] is not None
    assert info['mb_url'] is not None
    assert 'musicbrainz.org' in info['mb_url']


def test_parse_single_match_discogs(fixtures_dir):
    """Test parsing single Discogs match from fixture"""
    try:
        content, metadata = load_fixture(fixtures_dir, "single_match_discogs.txt")
    except:
        pytest.skip("Fixture single_match_discogs.txt not available")
        return
    
    info = parse_beet_match_info(content)
    
    # Verify Discogs specific
    assert info['discogs_id'] is not None
    assert info['discogs_url'] is not None
    assert 'discogs.com' in info['discogs_url']


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Multiple Candidates Parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_parse_multiple_candidates_musicbrainz(fixtures_dir):
    """Test parsing multiple MusicBrainz candidates from fixture"""
    try:
        content, metadata = load_fixture(fixtures_dir, "multiple_candidates_mb.txt")
    except:
        pytest.skip("Fixture multiple_candidates_mb.txt not available")
        return
    
    candidates = parse_verbose_candidates(content)
    
    # Verify we got candidates
    assert len(candidates) > 0
    
    # Verify structure of first candidate
    first = candidates[0]
    assert 'similarity' in first
    assert 'artist' in first
    assert 'album' in first
    assert first['mb_id'] is not None or first['discogs_id'] is not None


def test_parse_multiple_candidates_discogs(fixtures_dir):
    """Test parsing multiple Discogs candidates from fixture"""
    try:
        content, metadata = load_fixture(fixtures_dir, "multiple_candidates_discogs.txt")
    except:
        pytest.skip("Fixture multiple_candidates_discogs.txt not available")
        return
    
    candidates = parse_verbose_candidates(content)
    
    assert len(candidates) > 0
    
    # At least one should have Discogs ID
    has_discogs = any(c.get('discogs_id') for c in candidates)
    assert has_discogs, "No Discogs IDs found in candidates"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Full Output Parsing (Integration)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_parse_full_output_single_match(fixtures_dir):
    """Test full parsing of single match output"""
    try:
        content, metadata = load_fixture(fixtures_dir, "single_match_mb.txt")
    except:
        pytest.skip("Fixture not available")
        return
    
    result = parse_beet_output(content, "", "/test/path")
    
    # Verify canonical structure
    assert result['status'] == 'single_match'
    assert result['path'] == '/test/path'
    assert result['single_match'] is not None
    assert result['candidates'] == []
    assert result['has_multiple_candidates'] is False
    
    # Verify single match content
    match = result['single_match']
    assert match['similarity'] is not None
    assert match['artist'] is not None


def test_parse_full_output_multiple_candidates(fixtures_dir):
    """Test full parsing of multiple candidates output"""
    try:
        content, metadata = load_fixture(fixtures_dir, "multiple_candidates_mb.txt")
    except:
        pytest.skip("Fixture not available")
        return
    
    result = parse_beet_output(content, "", "/test/path")
    
    # Verify canonical structure
    assert result['status'] == 'has_candidates'
    assert result['has_multiple_candidates'] is True
    assert len(result['candidates']) > 0
    assert result['single_match'] is None


def test_parse_full_output_no_match(fixtures_dir):
    """Test full parsing of no match output"""
    try:
        content, metadata = load_fixture(fixtures_dir, "no_match.txt")
    except:
        pytest.skip("Fixture not available")
        return
    
    result = parse_beet_output(content, "", "/test/path")
    
    assert result['status'] == 'no_match'
    assert result['single_match'] is None
    assert result['candidates'] == []


def test_parse_full_output_success(fixtures_dir):
    """Test full parsing of successful import"""
    try:
        content, metadata = load_fixture(fixtures_dir, "success.txt")
    except:
        pytest.skip("Fixture not available")
        return
    
    result = parse_beet_output(content, "", "/test/path")
    
    assert result['status'] == 'success'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Edge Cases
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_parse_empty_output():
    """Test parsing empty output"""
    result = parse_beet_output("", "", "/test/path")
    
    assert result['status'] == 'needs_input'
    assert result['path'] == '/test/path'


def test_parse_malformed_output():
    """Test parsing malformed output"""
    malformed = "This is not valid beet output at all!"
    result = parse_beet_output(malformed, "", "/test/path")
    
    # Should not crash, should return needs_input
    assert result['status'] == 'needs_input'


def test_parse_unicode_output():
    """Test parsing output with unicode characters"""
    unicode_text = """
    Match (95.5%):
      Björk - Homogenic
    * Artist: Björk
    * Album: Homögenic
    """
    
    result = parse_beet_output(unicode_text, "", "/test/path")
    
    # Should handle unicode without crashing
    assert result is not None
    assert isinstance(result, dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Regression Tests (Add failed cases here)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_regression_null_values():
    """
    Regression: Ensure null/None values don't crash formatting
    Issue: Some fields could be None in parsed output
    """
    result = {
        'status': 'single_match',
        'path': '/test',
        'single_match': {
            'artist': None,  # Could be None
            'album': None,
            'year': None,
            'similarity': None,
        },
        'candidates': [],
        'has_multiple_candidates': False,
    }
    
    # Should not crash when formatting
    from ui.messages import format_import_status
    msg = format_import_status(result)
    
    assert msg is not None
    assert '?' in msg  # Should show fallback for None values


def test_regression_discogs_id_formats():
    """
    Regression: Multiple Discogs ID formats should all work
    Issue: Some IDs had 'r' prefix, some didn't
    """
    formats = [
        "r123456",
        "m123456", 
        "123456",
        "(123456)",
        "discogs.com/release/123456"
    ]
    
    for fmt in formats:
        result = extract_discogs_id(fmt)
        assert result is not None, f"Failed to extract from: {fmt}"
        assert result.startswith(('r', 'm')), f"Missing prefix in: {result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
