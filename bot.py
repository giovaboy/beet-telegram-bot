#!/usr/bin/env python3
"""
Beet Telegram Bot - Interactive music import manager
Entry point principale
"""
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from config import TELEGRAM_TOKEN, setup_logging
from core.beet_manager import BeetImportManager
from handlers.commands import start, list_imports, status, cancel_import
from handlers.callbacks import button_callback
from handlers.messages import handle_message

# Setup logging
logger = setup_logging()

# Inizializza il manager
manager = BeetImportManager()

def main():
    """Avvia il bot"""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN non configurato!")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Wrapper functions per passare il manager
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
    
    # Registra handlers
    app.add_handler(CommandHandler("start", start_wrapper))
    app.add_handler(CommandHandler("list", list_wrapper))
    app.add_handler(CommandHandler("status", status_wrapper))
    app.add_handler(CommandHandler("cancel", cancel_wrapper))
    app.add_handler(CallbackQueryHandler(callback_wrapper))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_wrapper))
    
    logger.info("🤖 Bot avviato!")
    app.run_polling()

if __name__ == '__main__':
    main()