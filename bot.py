#!/usr/bin/env python3
"""
Beet Telegram Bot - Interactive music import manager
Main entry point
"""
from telegram import MenuButtonCommands, BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PersistenceInput,
    PicklePersistence,
    filters,
)
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, CUSTOM_COMMANDS, PERSISTENCE_FILE, setup_logging
from i18n.translations import t
from core.beet_manager import BeetImportManager
from core.plugin_detector import get_plugin_detector
from handlers.commands import start, list_imports, status, cancel_import, execute_custom_command
from handlers.callbacks import button_callback
from handlers.messages import handle_message

# Setup logging
logger = setup_logging()

# Initialize the manager
manager = BeetImportManager()


async def on_error(update: object, context) -> None:
    """Log every unhandled error; tell the user instead of failing silently."""
    error = context.error
    # Transient connection problems with Telegram: the library retries by itself
    if isinstance(error, NetworkError) and not isinstance(error, BadRequest):
        logger.warning(f"Telegram network error: {error}")
        return

    logger.error("Unhandled error while processing an update", exc_info=error)

    chat = update.effective_chat if isinstance(update, Update) else None
    if chat is None or str(chat.id) != str(TELEGRAM_CHAT_ID):
        return
    try:
        await context.bot.send_message(chat_id=chat.id, text=t('panel.error'),
                                       parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Could not notify the user about the error: {e}")


def main():
    """Starts the bot"""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not configured!")

    # 🎯 NEW: Initialize plugin detector on startup
    logger.info("🔍 Detecting beet plugins...")
    detector = get_plugin_detector()
    plugins = detector.get_enabled_plugins()
    logger.info(f"✅ Detected plugins: {', '.join(plugins) if plugins else 'none'}")

    # Log metadata sources
    sources = detector.get_metadata_sources()
    logger.info(f"📚 Available metadata sources: {', '.join(sources)}")

    # Keep per-chat state (active panel, list snapshot, pending prompts) across restarts,
    # so buttons shown before a restart keep working instead of breaking the flow.
    persistence = PicklePersistence(
        filepath=PERSISTENCE_FILE,
        store_data=PersistenceInput(bot_data=False, chat_data=False, user_data=True, callback_data=False),
    )
    # Updates are processed one at a time (PTB default): a click is always
    # evaluated against the panel as left by the previous one.
    app = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    app.add_error_handler(on_error)

    # Wrapper functions to pass the manager instance
    async def start_wrapper(update, context):
        await start(update, context, manager)

    async def list_wrapper(update, context):
        await list_imports(update, context, manager)

    async def status_wrapper(update, context):
        await status(update, context, manager)

    async def cancel_wrapper(update, context):
        await cancel_import(update, context, manager)

    async def callback_wrapper(update, context):
        await button_callback(update, context, manager)

    async def message_wrapper(update, context):
        await handle_message(update, context, manager)

    # Register core handlers
    app.add_handler(CommandHandler("start", start_wrapper))
    app.add_handler(CommandHandler("list", list_wrapper))
    app.add_handler(CommandHandler("status", status_wrapper))
    app.add_handler(CommandHandler("cancel", cancel_wrapper))

    # 🔧 Register custom commands dynamically
    for item in CUSTOM_COMMANDS:
        cmd_name = item["cmd"]
        app.add_handler(CommandHandler(cmd_name, execute_custom_command))
        logger.info(f"Registered custom command: /{cmd_name} -> {item['action']}")

    app.add_handler(CallbackQueryHandler(callback_wrapper))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_wrapper))

    # ⚙️ Setup bot menu and commands list after init
    async def post_init(application):
        try:
            # Define core commands
            core_commands = [
                BotCommand("start", "Start the bot"),
                BotCommand("list", "Show ongoing imports"),
                BotCommand("status", "Check import status"),
                BotCommand("cancel", "Cancel an import"),
            ]

            # Add any custom commands
            custom_bot_commands = [
                BotCommand(item["cmd"], item.get("desc", f"Execute {item['action']}"))
                for item in CUSTOM_COMMANDS
            ]

            all_commands = core_commands + custom_bot_commands

            await application.bot.set_my_commands(all_commands)
            await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

            logger.info(f"✅ Set {len(all_commands)} commands in bot menu.")
        except Exception as e:
            logger.warning(f"⚠️ Error during menu button configuration: {e}")

    app.post_init = post_init

    logger.info("🤖 Bot started!")
    app.run_polling()


if __name__ == "__main__":
    main()