"""
Handler for text messages (ID input, confirmations)
"""
import subprocess
from telegram import Update
from telegram.ext import ContextTypes
from i18n.translations import t
from config import BEET_CONTAINER, BEET_USER
from handlers.commands import cleanup_old_status_message # Added import for the cleanup function

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, manager):
    """Handler for messages (entered IDs)"""
    if 'waiting_for' not in context.user_data or not manager.current_import:
        return
    
    text = update.message.text.strip()
    waiting_for = context.user_data['waiting_for']
    
    if waiting_for == 'mb_id':
        await update.message.reply_text(t('import.with_mb_id'))
        
        # NOTE: Missing cleanup_old_status_message call here, should be added for consistency
        # Assuming manager.import_with_id handles the BEETS interaction and returns a result
        result = manager.import_with_id(
            manager.current_import['path'],
            mb_id=text
        )
        await update.message.reply_text(result['message'])
        
        if result['status'] == 'success':
            # Add cleanup for successful import here
            await cleanup_old_status_message(context, update.message.chat_id, 'completed_label')
            manager.clear_state()
        
    elif waiting_for == 'discogs_id':
        await update.message.reply_text(t('import.with_discogs_id'))
        result = manager.import_with_id(
            manager.current_import['path'],
            discogs_id=text
        )
        await update.message.reply_text(result['message'])
        
        if result['status'] == 'success':
            # Add cleanup for successful import here
            await cleanup_old_status_message(context, update.message.chat_id, 'completed_label')
            manager.clear_state()
    
    elif waiting_for == 'confirm_as_is':
        # Check if the user confirms the "as-is" import
        
        if text.upper() in ['SI', 'YES', 'Y', 'OK']:
            await update.message.reply_text(t('import.as_is'))
            try:
                beet_path = manager.translate_path_for_beet(manager.current_import['path'])
                beet_cmd = ['beet', 'import', beet_path]
                
                if BEET_CONTAINER:
                    cmd = ['docker', 'exec', '-i']
                    if BEET_USER:
                        cmd.extend(['-u', BEET_USER])
                    cmd.extend([BEET_CONTAINER] + beet_cmd)
                else:
                    cmd = beet_cmd
                
                result = subprocess.run(cmd, input='U\n', capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    # Cleanup old message before confirming
                    await cleanup_old_status_message(context, update.message.chat_id, 'completed_label')
                    await update.message.reply_text(t('status.import_completed'))
                    manager.clear_state()
                else:
                    await update.message.reply_text(f"❌ {result.stderr[:200]}")
            except Exception as e:
                await update.message.reply_text(f"❌ {str(e)}")
        else:
            await update.message.reply_text(t('prompts.cancelled'))
    
    del context.user_data['waiting_for']
