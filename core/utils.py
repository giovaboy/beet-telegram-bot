"""
Utility helpers for Telegram bot and beet operations
"""
import logging
from telegram import Bot

logger = logging.getLogger(__name__)

# ======================================================
# 💬 TELEGRAM MESSAGE HELPERS
# ======================================================

async def send_temp_message(bot: Bot, chat_id: int, text: str, parse_mode='Markdown'):
    """
    Sends a temporary message and returns the message object.
    Useful for 'Analyzing...', 'Importing...' etc.
    """
    try:
        msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        return msg
    except Exception as e:
        logger.error(f"Failed to send temp message: {e}")
        return None


async def safe_delete_message(bot: Bot, chat_id: int, message_id: int):
    """
    Tries to delete a Telegram message safely, ignoring common errors.
    """
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.debug(f"Deleted message {message_id}")
    except Exception as e:
        # Ignora errori comuni come "message to delete not found"
        if "message to delete not found" not in str(e).lower():
            logger.warning(f"Could not delete message {message_id}: {e}")


async def remove_keyboard(query):
    """
    Removes the inline keyboard from a message if possible.
    """
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        logger.debug(f"Keyboard removed from message {query.message.message_id}")
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Could not remove keyboard: {e}")


# ======================================================
# 🧹 CLEANUP HELPERS
# ======================================================

async def cleanup_user_messages(context, chat_id: int, keys: list[str]):
    """
    Deletes any previously stored messages (by message_id) in user_data.
    Example:
        await cleanup_user_messages(context, chat_id, ['file_list_message_id', 'images_final_message_id'])
    """
    for key in keys:
        msg_id = context.user_data.get(key)
        if not msg_id:
            continue
        try:
            if isinstance(msg_id, list):
                for mid in msg_id:
                    await safe_delete_message(context.bot, chat_id, mid)
            else:
                await safe_delete_message(context.bot, chat_id, msg_id)
            logger.debug(f"Cleaned up messages for key '{key}'")
        except Exception as e:
            logger.warning(f"Failed to cleanup messages for '{key}': {e}")
        finally:
            context.user_data.pop(key, None)


def clear_user_temp_data(context):
    """
    Clears transient message IDs or temp data from user_data.
    """
    for key in ['file_list_message_id', 'file_list_message_ids', 'images_final_message_id']:
        context.user_data.pop(key, None)


# ======================================================
# 📏 TEXT & LOGGING UTILITIES
# ======================================================

def truncate_for_telegram(text: str, limit: int = 4000) -> list[str]:
    """
    Splits long text into Telegram-safe chunks (≤4096 chars).
    Returns a list of parts to be sent sequentially.
    """
    if not text:
        return []

    parts, current = [], ""
    for line in text.splitlines():
        if len(current) + len(line) + 1 <= limit:
            current += line + "\n"
        else:
            parts.append(current)
            current = line + "\n"
    if current:
        parts.append(current)
    return parts


def log_exception(context: str, e: Exception):
    """Helper for consistent error logging."""
    logger.error(f"[{context}] {type(e).__name__}: {e}")
