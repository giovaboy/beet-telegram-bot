#!/usr/bin/env python3
"""
Beet Telegram Bot - Interactive music import manager
Main entry point
"""
from telegram import MenuButtonCommands, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from config import TELEGRAM_TOKEN, CUSTOM_COMMANDS, setup_logging
from core.beet_manager import BeetImportManager
from handlers.commands import start, list_imports, status, cancel_import, execute_custom_command
from handlers.callbacks import button_callback
from handlers.messages import handle_message

# Setup logging
logger = setup_logging()

# Initialize the manager
manager = BeetImportManager()


def main():
    """Starts the bot"""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not configured!")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

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
            # Definisce i comandi base
            core_commands = [
                BotCommand("start", "Avvia il bot"),
                BotCommand("list", "Mostra gli import in corso"),
                BotCommand("status", "Verifica lo stato di un import"),
                BotCommand("cancel", "Annulla un import"),
            ]

            # Aggiunge eventuali comandi custom
            custom_bot_commands = [
                BotCommand(item["cmd"], item.get("desc", f"Esegui {item['action']}"))
                for item in CUSTOM_COMMANDS
            ]

            all_commands = core_commands + custom_bot_commands

            await application.bot.set_my_commands(all_commands)
            await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

            logger.info(f"✅ Impostati {len(all_commands)} comandi nel menu del bot.")
        except Exception as e:
            logger.warning(f"⚠️ Errore durante la configurazione del menu button: {e}")

    app.post_init = post_init

    logger.info("🤖 Bot started!")
    app.run_polling()


if __name__ == "__main__":
    main()