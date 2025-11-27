"""
Cortex Unified Bot - Main Bot Orchestrator
Manages Telegram bot initialization and handler registration
"""

import asyncio
import logging
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)

from config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

# Conversation states for wallet import
IMPORT_METHOD, IMPORT_DATA = range(2)


class CortexBot:
    """Main bot orchestrator"""
    
    def __init__(self, database, ai_handler, bot_handlers):
        """Initialize bot components"""
        self.db = database
        self.ai_handler = ai_handler
        self.bot_handlers = bot_handlers
        
        # Build application
        logger.info("Building Telegram application...")
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Register handlers
        self._register_handlers()
        
        logger.info("✅ Cortex bot initialized")
    
    def _register_handlers(self):
        """Register all command and message handlers"""
        
        # Command handlers (only 3 commands)
        self.application.add_handler(
            CommandHandler("start", self.bot_handlers.start_command)
        )
        
        self.application.add_handler(
            CommandHandler("createwallet", self.bot_handlers.create_wallet_command)
        )

        self.application.add_handler(
            CommandHandler("debugchannel", self.bot_handlers.debug_channel_command)
        )
        
        # Import wallet conversation handler
        import_conv = ConversationHandler(
            entry_points=[
                CommandHandler("importwallet", self.bot_handlers.import_wallet_start)
            ],
            states={
                IMPORT_METHOD: [
                    CallbackQueryHandler(
                        self.bot_handlers.import_method_callback,
                        pattern="^import_"
                    )
                ],
                IMPORT_DATA: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.bot_handlers.import_data_handler
                    ),
                    CommandHandler("cancel", self.bot_handlers.import_cancel)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.bot_handlers.import_cancel)],
            per_message=False,
            per_chat=True,
            per_user=True
        )
        
        self.application.add_handler(import_conv)
        
        # Callback query handlers
        
        # Signal swap buttons (from Part 1)
        self.application.add_handler(
            CallbackQueryHandler(
                self.bot_handlers.signal_swap_callback,
                pattern="^signal_swap_"
            )
        )

        # Phone verification callbacks
        self.application.add_handler(
            CallbackQueryHandler(
                self.bot_handlers.verification_callback_handler,
                pattern="^verify_"
            )
        )
        
        # AI-driven swap confirmation (from Part 2)
        self.application.add_handler(
            CallbackQueryHandler(
                self.bot_handlers.swap_callback_handler,
                pattern="^swap_"
            )
        )
        
        # Payment confirmation
        self.application.add_handler(
            CallbackQueryHandler(
                self.bot_handlers.payment_callback_handler,
                pattern="^pay_"
            )
        )
        
        # Message handler for AI (must be last)
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.bot_handlers.handle_ai_message
            )
        )
        
        # Error handler
        self.application.add_error_handler(self._error_handler)
        
        logger.info("✅ All handlers registered")
    
    async def _error_handler(self, update, context):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        
        # Notify user of error
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ An error occurred. Please try again or contact support."
                )
        except Exception as e:
            logger.error(f"Could not send error message: {e}")
    
    async def run(self):
        """Start the bot with polling"""
        logger.info("🚀 Starting bot polling...")
        
        # Initialize bot
        await self.application.initialize()
        await self.application.start()
        
        # Start polling
        await self.application.updater.start_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True
        )
        
        # Keep running until stopped
        try:
            # This is the fix - we need to keep the bot running
            await asyncio.Event().wait()  # Wait forever
        finally:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()