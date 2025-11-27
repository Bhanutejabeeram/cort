"""
Cortex Unified Bot - Main Entry Point
Production-Ready Telegram Bot for Solana DeFi with AI and Channel Monitoring
"""

import os
import sys
import asyncio
import logging
import threading
from dotenv import load_dotenv
from loguru import logger

# Load environment variables
load_dotenv()

# Configure loguru
logger.add(
    os.getenv("LOG_FILE", "logs/cortex.log"),
    rotation="500 MB",
    retention="10 days",
    level=os.getenv("LOG_LEVEL", "INFO")
)

# Configure standard logging for libraries
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def main():
    """Main entry point for Cortex bot"""
    try:
        logger.info("="*60)
        logger.info("🚀 STARTING CORTEX UNIFIED BOT")
        logger.info("="*60)
        
        # Validate environment variables
        from config import (
            TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH,
            OPENAI_API_KEY, MONGODB_URI, ENCRYPTION_KEY, REDIS_URL
        )
        
        required_vars = {
            "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
            "TELEGRAM_API_ID": TELEGRAM_API_ID,
            "TELEGRAM_API_HASH": TELEGRAM_API_HASH,
            "OPENAI_API_KEY": OPENAI_API_KEY,
            "MONGODB_URI": MONGODB_URI,
            "ENCRYPTION_KEY": ENCRYPTION_KEY,
            "REDIS_URL": REDIS_URL
        }
        
        missing = [k for k, v in required_vars.items() if not v or v == "0"]
        if missing:
            logger.error(f"❌ Missing required environment variables: {', '.join(missing)}")
            logger.info("Please check your .env file")
            return
        
        logger.info("✅ Environment variables validated")
        logger.info(f"📡 Redis: Upstash (URL configured)")
        
        # Initialize database
        from database import Database
        from config import MONGODB_URI, MONGODB_DATABASE, MONGODB_COLLECTION, ENCRYPTION_KEY
        
        logger.info("Connecting to MongoDB...")
        db = Database(MONGODB_URI, MONGODB_DATABASE, MONGODB_COLLECTION, ENCRYPTION_KEY)
        logger.info("✅ Database connected")
        
        # Initialize core components
        from core.ai_handler import AIHandler
        from core.bot_handlers import BotHandlers
        from core.bot import CortexBot
        
        logger.info("Initializing AI handler...")
        ai_handler = AIHandler(db)
        logger.info("✅ AI handler initialized")
        
        logger.info("Initializing bot handlers...")
        bot_handlers = BotHandlers(db, ai_handler)
        logger.info("✅ Bot handlers initialized")
        
        # Initialize and start the bot
        logger.info("Initializing Telegram bot...")
        bot = CortexBot(db, ai_handler, bot_handlers)
        
        # Initialize channel monitoring (runs in parallel)
        from monitoring.channel_monitor import ChannelMonitor
        monitor = None
        
        if TELEGRAM_API_HASH and TELEGRAM_API_HASH != "your_api_hash_here":
            logger.info("Initializing channel monitoring...")
            monitor = ChannelMonitor(db)
            
            # Set signal callback
            def on_signal_detected(signal_data):
                """Callback when a buy signal is detected"""
                asyncio.create_task(bot_handlers.handle_signal_detected(signal_data))
            
            monitor.set_signal_callback(on_signal_detected)
            
            # Start monitor in background
            monitor_task = asyncio.create_task(monitor.run())
            logger.info("✅ Channel monitoring started")
        else:
            logger.warning("⚠️ Channel monitoring disabled (Telegram API credentials not configured)")
            monitor_task = None
        
        # Initialize Twilio webhook server
        webhook_thread = None
        
        try:
            from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, WEBHOOK_URL
            
            if (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and 
                TWILIO_ACCOUNT_SID != "ACxxxxx" and 
                WEBHOOK_URL and WEBHOOK_URL != "http://localhost:5000"):
                
                logger.info("Starting Twilio webhook server...")
                
                # Import Flask app from webhook module
                from services.twilio_webhook import app as webhook_app
                
                def run_webhook():
                    """Run Flask webhook server in background thread"""
                    try:
                        webhook_app.run(
                            host='0.0.0.0',
                            port=5000,
                            debug=False,
                            use_reloader=False  # Important: disable reloader in thread
                        )
                    except Exception as e:
                        logger.error(f"Webhook server error: {e}")
                
                # Start webhook in daemon thread
                webhook_thread = threading.Thread(target=run_webhook, daemon=True)
                webhook_thread.start()
                
                logger.info("✅ Twilio webhook server started on port 5000")
                logger.info(f"   Webhook URL: {WEBHOOK_URL}")
            else:
                logger.warning("⚠️ Twilio webhook not started (credentials or URL not configured)")
                
        except Exception as e:
            logger.warning(f"⚠️ Twilio webhook server not started: {e}")
        
        # Display Celery info
        logger.info("📋 Celery Configuration:")
        logger.info("   • Redis: Upstash (configured in celery_config.py)")
        logger.info("   • Queues: urgent, normal, low")
        logger.info("   • Workers: Start with 'celery -A celery_config worker -l info -Q urgent,normal,low'")
        
        # Start the bot
        logger.info("="*60)
        logger.info("🤖 CORTEX BOT IS NOW RUNNING")
        logger.info("💬 Send /start to your bot to begin")
        logger.info("="*60)
        logger.info("")
        logger.info("🔧 Running Services:")
        logger.info("   ✅ Telegram Bot")
        logger.info("   ✅ AI Handler (OpenAI)")
        logger.info("   ✅ MongoDB Database")
        if monitor:
            logger.info("   ✅ Channel Monitor (Telethon)")
        if webhook_thread and webhook_thread.is_alive():
            logger.info("   ✅ Twilio Webhook (Flask)")
        logger.info("")
        logger.info("⚠️  NOTE: Start Celery workers separately for background tasks!")
        logger.info("   Command: celery -A celery_config worker -l info -Q urgent,normal,low")
        logger.info("="*60)
        
        # Run bot
        await bot.run()
        
        # Wait for tasks to complete (they run forever)
        tasks = [t for t in [monitor_task] if t]
        if tasks:
            await asyncio.gather(*tasks)
        
    except KeyboardInterrupt:
        logger.info("⏹️ Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        raise
    finally:
        logger.info("🛑 Cortex bot shutdown complete")


if __name__ == "__main__":
    # Run the bot
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        sys.exit(1)