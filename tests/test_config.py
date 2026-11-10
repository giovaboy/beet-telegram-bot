"""Configuration parsing."""
import pytest


@pytest.mark.parametrize("value, expected", [
    (None, ('INFO', None)),
    ('DEBUG', ('DEBUG', None)),
    ('debug', ('DEBUG', None)),
    (' Info ', ('INFO', None)),
    ('warn', ('WARNING', None)),
    ('verbose', ('INFO', 'verbose')),
])
def test_log_level_is_tolerant(value, expected):
    from config import parse_log_level
    assert parse_log_level(value) == expected
