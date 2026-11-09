"""
Telegram utility functions for handling long messages and formatting
"""
import re
from typing import List, Optional
from telegram import Message
from telegram.error import BadRequest
from config import setup_logging

logger = setup_logging()

# Telegram limits
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
SAFE_MESSAGE_LENGTH = 4000  # Leave some margin for safety


def split_message(text: str, max_length: int = SAFE_MESSAGE_LENGTH) -> List[str]:
    """
    Split a long message into multiple parts that respect Telegram's limit.

    Tries to split at logical points:
    1. Paragraph breaks (\n\n)
    2. Line breaks (\n)
    3. Sentence breaks (. )
    4. Word breaks ( )
    5. Character breaks (last resort)

    Args:
        text: The text to split
        max_length: Maximum length per part (default: 4000)

    Returns:
        List of message parts, each under max_length
    """
    if len(text) <= max_length:
        return [text]

    parts = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            parts.append(remaining)
            break

        # Find the best split point
        chunk = remaining[:max_length]

        # Try to split at paragraph break
        split_point = chunk.rfind('\n\n')

        # If no paragraph break, try line break
        if split_point == -1:
            split_point = chunk.rfind('\n')

        # If no line break, try sentence break
        if split_point == -1:
            split_point = chunk.rfind('. ')
            if split_point != -1:
                split_point += 1  # Include the period

        # If no sentence break, try word break
        if split_point == -1:
            split_point = chunk.rfind(' ')

        # Last resort: split at max_length
        if split_point == -1:
            split_point = max_length

        # Add this chunk to parts
        parts.append(remaining[:split_point].strip())
        remaining = remaining[split_point:].strip()

    return parts


def truncate_message(text: str, max_length: int = SAFE_MESSAGE_LENGTH,
                     suffix: str = "\n\n_...message truncated_") -> str:
    """
    Truncate a message to fit within Telegram's limit.

    Args:
        text: The text to truncate
        max_length: Maximum length (default: 4000)
        suffix: Text to append when truncating (default: "...message truncated")

    Returns:
        Truncated text with suffix if needed
    """
    if len(text) <= max_length:
        return text

    # Reserve space for suffix
    available_length = max_length - len(suffix)

    # Try to truncate at a logical point
    truncated = text[:available_length]

    # Try to end at a line break
    last_newline = truncated.rfind('\n')
    if last_newline > available_length * 0.8:  # If we're not losing too much
        truncated = truncated[:last_newline]

    return truncated + suffix


async def send_long_message(message: Message, text: str,
                            parse_mode: Optional[str] = None,
                            **kwargs) -> List[Message]:
    """
    Send a potentially long message, automatically splitting if needed.

    Args:
        message: The original message object to reply to
        text: The text to send (can be longer than Telegram limit)
        parse_mode: Telegram parse mode ('MarkdownV2', 'HTML', etc.)
        **kwargs: Additional arguments to pass to reply_text

    Returns:
        List of sent Message objects
    """
    parts = split_message(text, SAFE_MESSAGE_LENGTH)
    sent_messages = []

    for i, part in enumerate(parts):
        try:
            # Add part indicator if multiple parts
            if len(parts) > 1:
                part_indicator = f"\n\n_\\[Part {i+1}/{len(parts)}\\]_" if parse_mode == 'MarkdownV2' else f"\n\n[Part {i+1}/{len(parts)}]"
                part_with_indicator = part + part_indicator
            else:
                part_with_indicator = part

            sent = await message.reply_text(
                part_with_indicator,
                parse_mode=parse_mode,
                **kwargs
            )
            sent_messages.append(sent)

        except BadRequest as e:
            if "message is too long" in str(e).lower():
                # Still too long? Truncate more aggressively
                logger.error(f"Part {i+1} still too long, truncating...")
                truncated = truncate_message(part, SAFE_MESSAGE_LENGTH - 200)
                sent = await message.reply_text(
                    truncated,
                    parse_mode=parse_mode,
                    **kwargs
                )
                sent_messages.append(sent)
            else:
                raise

    return sent_messages


def estimate_message_length(text: str, parse_mode: Optional[str] = None) -> int:
    """
    Estimate the actual length of a message after Telegram processing.

    Telegram may count some characters differently depending on parse mode.

    Args:
        text: The message text
        parse_mode: Parse mode being used

    Returns:
        Estimated length
    """
    # For MarkdownV2, escaped characters count as 2
    if parse_mode == 'MarkdownV2':
        # Count backslash-escaped characters
        escaped = re.findall(r'\\[_*\[\]()~`>#+=\-|{}.!]', text)
        return len(text) + len(escaped)

    return len(text)


def smart_truncate_for_preview(text: str, max_lines: int = 15,
                                max_length: int = SAFE_MESSAGE_LENGTH) -> str:
    """
    Intelligently truncate text for preview purposes.

    Useful for showing first N differences, first N lines, etc.

    Args:
        text: Text to truncate
        max_lines: Maximum number of lines to show
        max_length: Maximum total length

    Returns:
        Truncated text with indicator if truncated
    """
    lines = text.split('\n')

    # Truncate by lines first
    if len(lines) > max_lines:
        truncated_lines = lines[:max_lines]
        remaining_lines = len(lines) - max_lines
        result = '\n'.join(truncated_lines)
        result += f"\n\n_...and {remaining_lines} more lines_"
    else:
        result = text

    # Then check total length
    if len(result) > max_length:
        result = truncate_message(result, max_length)

    return result


def format_large_list(items: List[str], max_items: int = 10,
                     prefix: str = "• ",
                     more_template: str = "\n\n_...and {count} more items_") -> str:
    """
    Format a list of items, truncating if too many.

    Args:
        items: List of items to format
        max_items: Maximum items to show
        prefix: Prefix for each item (default: bullet point)
        more_template: Template for "more items" message

    Returns:
        Formatted list string
    """
    if not items:
        return "_No items_"

    shown_items = items[:max_items]
    result = '\n'.join(f"{prefix}{item}" for item in shown_items)

    if len(items) > max_items:
        remaining = len(items) - max_items
        result += more_template.format(count=remaining)

    return result


def preserve_markdown_in_split(text: str, max_length: int = SAFE_MESSAGE_LENGTH) -> List[str]:
    """
    Split message while trying to preserve MarkdownV2 formatting.

    Ensures code blocks, bold, italic, etc. are properly closed in each part.

    Args:
        text: Text with MarkdownV2 formatting
        max_length: Max length per part

    Returns:
        List of parts with preserved formatting
    """
    # This is complex - for now, use basic split
    # TODO: Implement proper markdown-aware splitting
    parts = split_message(text, max_length)

    # Basic check: if a part starts mid-code block, prepend ```
    # and if it ends mid-code block, append ```
    for i in range(len(parts)):
        # Count backticks before this part
        prev_text = ''.join(parts[:i])
        backticks_before = prev_text.count('```')

        # If odd number, we're inside a code block
        if backticks_before % 2 == 1:
            parts[i] = '```\n' + parts[i]

        # Count backticks in this part
        backticks_in_part = parts[i].count('```')
        total_backticks = backticks_before + backticks_in_part

        # If still odd, close the code block
        if total_backticks % 2 == 1:
            parts[i] = parts[i] + '\n```'

    return parts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Convenience Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def safe_reply(message: Message, text: str, parse_mode: Optional[str] = None,
                     **kwargs) -> List[Message]:
    """
    Safe reply that automatically handles long messages.

    Drop-in replacement for message.reply_text() that handles splitting.

    Example:
        # Instead of:
        await message.reply_text(long_text, parse_mode='MarkdownV2')

        # Use:
        await safe_reply(message, long_text, parse_mode='MarkdownV2')

    Args:
        message: Original message to reply to
        text: Text to send (any length)
        parse_mode: Parse mode
        **kwargs: Additional arguments for reply_text

    Returns:
        List of sent messages
    """
    return await send_long_message(message, text, parse_mode, **kwargs)


def is_message_too_long(text: str, parse_mode: Optional[str] = None) -> bool:
    """
    Check if a message would exceed Telegram's limit.

    Args:
        text: Message text
        parse_mode: Parse mode being used

    Returns:
        True if message is too long
    """
    estimated_length = estimate_message_length(text, parse_mode)
    return estimated_length > TELEGRAM_MAX_MESSAGE_LENGTH