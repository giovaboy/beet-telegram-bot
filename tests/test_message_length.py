#!/usr/bin/env python3
"""
Test suite for message length handling
Ensures messages never exceed Telegram limits
"""
import pytest
from core.telegram_utils import (
    split_message,
    truncate_message,
    is_message_too_long,
    estimate_message_length,
    TELEGRAM_MAX_MESSAGE_LENGTH,
    SAFE_MESSAGE_LENGTH
)
from ui.messages import format_import_status


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Message Splitting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_split_short_message():
    """Short messages should not be split"""
    short_text = "This is a short message"
    parts = split_message(short_text)

    assert len(parts) == 1
    assert parts[0] == short_text


def test_split_long_message():
    """Long messages should be split"""
    # Create a message longer than limit
    long_text = "Line\n" * 5000  # ~25000 chars
    parts = split_message(long_text, max_length=4000)

    assert len(parts) > 1

    # Each part should be under limit
    for part in parts:
        assert len(part) <= 4000


def test_split_respects_paragraphs():
    """Splitting should prefer paragraph breaks"""
    text = "Paragraph 1\n\nParagraph 2\n\nParagraph 3" * 200
    parts = split_message(text, max_length=1000)

    # Each part should end with paragraph break (if possible)
    for part in parts[:-1]:  # All except last
        # Should end with newline or be at natural break
        assert part.endswith('\n') or len(part) < 1000


def test_split_preserves_content():
    """Split parts should contain all original content"""
    original = "Content " * 1000
    parts = split_message(original, max_length=2000)

    # Reconstruct
    reconstructed = ' '.join(parts)

    # Should contain same words (order may differ due to splitting)
    original_words = set(original.split())
    reconstructed_words = set(reconstructed.split())

    assert original_words == reconstructed_words


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Message Truncation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_truncate_short_message():
    """Short messages should not be truncated"""
    short_text = "Short message"
    result = truncate_message(short_text)

    assert result == short_text
    assert "truncated" not in result


def test_truncate_long_message():
    """Long messages should be truncated with suffix"""
    long_text = "x" * 5000
    result = truncate_message(long_text, max_length=1000)

    assert len(result) <= 1000
    assert "truncated" in result


def test_truncate_custom_suffix():
    """Should use custom suffix when provided"""
    long_text = "x" * 5000
    custom_suffix = "...END"
    result = truncate_message(long_text, max_length=1000, suffix=custom_suffix)

    assert result.endswith(custom_suffix)
    assert len(result) <= 1000


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Length Checking
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_is_message_too_long():
    """Should correctly identify too-long messages"""
    short_text = "Short"
    long_text = "x" * (TELEGRAM_MAX_MESSAGE_LENGTH + 1)

    assert not is_message_too_long(short_text)
    assert is_message_too_long(long_text)


def test_estimate_markdown_length():
    """Should estimate MarkdownV2 escaped text correctly"""
    # Text with escaped characters
    text = "Test\\! Test\\."  # 2 escaped chars

    estimated = estimate_message_length(text, parse_mode='MarkdownV2')

    # Should count escaped chars as 2
    assert estimated >= len(text)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Real Import Status Messages
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_single_match_message_length():
    """Single match messages should be under limit"""
    # Create result with many differences
    result = {
        'status': 'single_match',
        'path': '/test/Very_Long_Album_Name_That_Is_Really_Long',
        'single_match': {
            'similarity': 95.5,
            'artist': 'Very Long Artist Name',
            'album': 'Very Long Album Name With Many Words',
            'year': 2020,
            'differences': [
                f'≠ track #{i}: Long Track Name (3:45) -> Long Track Name (3:46)'
                for i in range(100)  # Many differences
            ],
            'mb_id': 'abc123-def456-ghi789',
            'mb_url': 'https://musicbrainz.org/release/abc123'
        },
        'candidates': [],
        'has_multiple_candidates': False
    }

    msg = format_import_status(result)

    # Should be under safe limit
    assert len(msg) < SAFE_MESSAGE_LENGTH, f"Message is {len(msg)} chars, limit is {SAFE_MESSAGE_LENGTH}"


def test_multiple_candidates_message_length():
    """Multiple candidates messages should be under limit"""
    # Create result with many candidates
    candidates = []
    for i in range(50):  # Many candidates
        candidates.append({
            'number': i + 1,
            'similarity': 80 + i * 0.1,
            'artist': f'Artist {i}',
            'album': f'Very Long Album Name Number {i}',
            'year': 2020 + i,
            'mb_id': f'id-{i}',
            'mb_url': f'https://musicbrainz.org/release/id-{i}'
        })

    result = {
        'status': 'has_candidates',
        'path': '/test/Album',
        'single_match': None,
        'candidates': candidates,
        'has_multiple_candidates': True
    }

    msg = format_import_status(result)

    # Should be under safe limit (will truncate if needed)
    assert len(msg) < SAFE_MESSAGE_LENGTH, f"Message is {len(msg)} chars, limit is {SAFE_MESSAGE_LENGTH}"


def test_error_message_length():
    """Error messages with long output should be handled"""
    # Create result with long output
    long_output = "\n".join([f"Error line {i}" for i in range(1000)])

    result = {
        'status': 'error',
        'path': '/test/Album',
        'single_match': None,
        'candidates': [],
        'has_multiple_candidates': False,
        'output': long_output
    }

    msg = format_import_status(result)

    # Should be truncated to safe limit
    assert len(msg) < SAFE_MESSAGE_LENGTH, f"Message is {len(msg)} chars, limit is {SAFE_MESSAGE_LENGTH}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Edge Cases
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_empty_message():
    """Empty messages should be handled"""
    parts = split_message("")
    assert len(parts) == 1
    assert parts[0] == ""


def test_message_at_exact_limit():
    """Messages exactly at limit should not be split"""
    text = "x" * SAFE_MESSAGE_LENGTH
    parts = split_message(text, max_length=SAFE_MESSAGE_LENGTH)

    assert len(parts) == 1


def test_message_one_over_limit():
    """Messages 1 char over limit should be split"""
    text = "x" * (SAFE_MESSAGE_LENGTH + 1)
    parts = split_message(text, max_length=SAFE_MESSAGE_LENGTH)

    assert len(parts) == 2


def test_unicode_handling():
    """Should handle unicode characters correctly"""
    unicode_text = "Unicode: 🎵 Björk – Homögenic 中文" * 100
    parts = split_message(unicode_text, max_length=1000)

    # Should split without breaking unicode
    for part in parts:
        # Should be valid UTF-8
        part.encode('utf-8')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Performance
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_split_performance():
    """Splitting should be reasonably fast"""
    import time

    # Create large message
    large_text = "Line\n" * 10000  # ~50KB

    start = time.time()
    parts = split_message(large_text, max_length=4000)
    elapsed = time.time() - start

    # Should complete in reasonable time
    assert elapsed < 1.0, f"Splitting took {elapsed:.2f}s, should be < 1s"

    # Should produce valid splits
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= 4000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])