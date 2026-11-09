"""Minimal MarkdownV2 checker: flags what Telegram rejects with \"can't parse entities\"."""

_ALWAYS_ESCAPED = set('#+-={}.!')


def markdown_v2_errors(text):
    """Unescaped reserved characters outside code/pre/link URLs (what Telegram rejects)."""
    errors, i, n = [], 0, len(text)
    in_code = in_pre = in_url = False
    while i < n:
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if text.startswith('```', i):
            in_pre, i = not in_pre, i + 3
            continue
        if in_pre:
            i += 1
            continue
        if c == '`':
            in_code, i = not in_code, i + 1
            continue
        if in_code:
            i += 1
            continue
        if in_url:
            in_url = c != ')'
            i += 1
            continue
        if c == '(':
            if i > 0 and text[i - 1] == ']':
                in_url = True
            else:
                errors.append((i, c))
        elif c == ')' or c in _ALWAYS_ESCAPED:
            errors.append((i, c))
        elif c == '|':
            if text.startswith('||', i):
                i += 2
                continue
            errors.append((i, c))
        elif c == '>' and not _at_line_start(text, i):
            errors.append((i, c))
        i += 1
    return errors


def _at_line_start(text, i):
    """'>' starts a block quote at the beginning of a line ('**>' = expandable)."""
    start = text.rfind('\n', 0, i) + 1
    return text[start:i] in ('', '**')
