"""
Cortex Bot - Background Tasks (FIXED)
Only for: Calls, Swaps, Notifications (NO TELETHON OPERATIONS)
FIXED: Proper task registration and error handling
"""

import os
import logging
import asyncio
import json
import uuid
from datetime import datetime
from dotenv import load_dotenv
import redis

# Load environment first
load_dotenv()

# Import Celery app AFTER environment is loaded
from celery_config import celery_app

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s: %(levelname)s/%(name)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Import other dependencies
from telegram import Bot
from database import Database
from config import (
    MONGODB_URI, MONGODB_DATABASE, MONGODB_COLLECTION, 
    ENCRYPTION_KEY, TELEGRAM_BOT_TOKEN, REDIS_URL
)
from services.jupiter_swap import JupiterAPI
from services.twilio_calls import TwilioHandler

# Initialize services
logger.info("[TASKS] Initializing services...")
try:
    db = Database(MONGODB_URI, MONGODB_DATABASE, MONGODB_COLLECTION, ENCRYPTION_KEY)
    jupiter = JupiterAPI()
    twilio_handler = TwilioHandler(db)
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # Initialize Redis for shared call storage
    redis_client = redis.from_url(
        REDIS_URL,
        decode_responses=True,
        ssl_cert_reqs=None  # For Upstash
    )
    logger.info("[TASKS] ✅ All services initialized successfully")
except Exception as e:
    logger.error(f"[TASKS] ❌ Failed to initialize services: {e}")
    raise

# ==================== CALL TASKS ====================

@celery_app.task(bind=True, max_retries=2, queue='urgent', name='tasks.make_call_task')
def make_call_task(self, user_id: int, token_data: dict, channel_name: str):
    """
    HIGH PRIORITY: Make phone call for trading signal
    UPGRADED: Passes username for personalized greeting
    """
    try:
        logger.info(f"[TASK:CALL] ========== TASK STARTED ==========")
        logger.info(f"[TASK:CALL] User ID: {user_id}")
        logger.info(f"[TASK:CALL] Token: {token_data.get('symbol', 'Unknown')}")
        logger.info(f"[TASK:CALL] Channel: {channel_name}")
        
        # Get user
        user = db.get_user(user_id)
        if not user:
            logger.error(f"[TASK:CALL] User {user_id} not found in database")
            return {"success": False, "error": "User not found"}
        
        # Get username for personalized greeting
        username = user.get('username', None)
        logger.info(f"[TASK:CALL] User found: @{username or 'unknown'}")
        
        # Check if calls enabled
        if not user.get("calls_enabled", True):
            logger.info(f"[TASK:CALL] Calls disabled for user {user_id}")
            return {"success": False, "error": "Calls disabled"}
        
        # Check phone number
        phone_number = user.get("phone_number")
        logger.info(f"[TASK:CALL] Phone number: {phone_number if phone_number else 'NOT SET'}")
        
        if not phone_number:
            logger.info(f"[TASK:CALL] No phone number, sending Telegram notification instead")
            # Send Telegram notification instead
            try:
                asyncio.run(bot.send_message(
                    chat_id=user_id,
                    text=f"🚨 <b>BUY SIGNAL DETECTED</b>\n\n"
                         f"📡 Channel: @{channel_name}\n"
                         f"🪙 Token: {token_data.get('name')} ({token_data.get('symbol')})\n"
                         f"📍 Address: <code>{token_data.get('id')}</code>\n\n"
                         f"💰 Price: ${token_data.get('usdPrice', 0):.8f}\n"
                         f"📊 Market Cap: ${token_data.get('mcap', 0):,.0f}\n\n"
                         f"⚠️ Set your phone number to receive calls: /settings",
                    parse_mode="HTML"
                ))
                logger.info(f"[TASK:CALL] ✅ Telegram notification sent successfully")
            except Exception as notify_error:
                logger.error(f"[TASK:CALL] Failed to send Telegram notification: {notify_error}")
            
            return {"success": True, "notification_sent": True}
        
        # Extract token data for call script
        token_symbol = token_data.get('symbol', 'Unknown')
        token_name = token_data.get('name', 'Unknown Token')
        market_cap = token_data.get('mcap')
        
        # Make the call with personalized greeting
        logger.info(f"[TASK:CALL] Initiating phone call to {phone_number}")
        
        call_success = asyncio.run(
            twilio_handler.make_signal_call(
                phone_number=phone_number,
                token_symbol=token_symbol,
                token_name=token_name,
                channel_name=channel_name,
                price=None,  # Not used in script anymore
                market_cap=market_cap,
                username=username  # NEW: Pass username for personalized greeting
            )
        )
        
        if call_success:
            # Store call data in Redis (shared across processes)
            twilio_call_sid = twilio_handler.last_call_sid
            
            call_data = {
                "user_id": user_id,
                "token_data": token_data,
                "channel_name": channel_name,
                "timestamp": datetime.now().isoformat()
            }
            
            # Store with Twilio SID as key (webhook will use this)
            redis_key = f"active_call:{twilio_call_sid}"
            redis_client.setex(
                redis_key,
                3600,  # Expire after 1 hour
                json.dumps(call_data, default=str)
            )
            
            logger.info(f"[TASK:CALL] ✅ Call data stored in Redis with key: {redis_key}")
            
            # Update stats
            db.increment_call_stats(user_id, responded=False)
            
            logger.info(f"[TASK:CALL] ✅ Call initiated successfully - SID: {twilio_call_sid}")
            return {"success": True, "twilio_sid": twilio_call_sid}
        else:
            logger.error(f"[TASK:CALL] ❌ Call initiation failed")
            return {"success": False, "error": "Call failed"}
        
    except Exception as e:
        logger.error(f"[TASK:CALL] ❌ EXCEPTION: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        logger.info(f"[TASK:CALL] ========== TASK ENDED ==========")


# ==================== SWAP TASKS ====================

@celery_app.task(bind=True, max_retries=2, queue='urgent', name='tasks.execute_swap_task')
def execute_swap_task(self, user_id: int, token_address: str, amount_sol: float, channel_name: str = None):
    """
    HIGH PRIORITY: Execute token swap
    FIXED: Added explicit task name
    """
    try:
        logger.info(f"[TASK:SWAP] Starting swap for user {user_id}: {amount_sol} SOL → {token_address[:8]}...")
        
        # Get user
        user = db.get_user(user_id)
        if not user or not user.get("wallet_address"):
            logger.error(f"[TASK:SWAP] No wallet for user {user_id}")
            return {"success": False, "error": "Wallet not found"}
        
        wallet_address = user["wallet_address"]
        private_key = db.get_decrypted_private_key(user_id)
        
        if not private_key:
            logger.error(f"[TASK:SWAP] Could not decrypt private key")
            return {"success": False, "error": "Could not decrypt wallet"}
        
        # Execute swap via Jupiter
        result = asyncio.run(
            jupiter.execute_swap(
                wallet_address=wallet_address,
                private_key=private_key,
                input_token="SOL",
                output_token=token_address,
                amount=str(amount_sol),
                slippage_percent=user.get("slippage_percent", 5)
            )
        )
        
        if result.get("success"):
            signature = result["signature"]
            output_amount = result.get("output_amount", 0)
            
            # Save to database
            db.add_transaction(user_id, {
                "tx_id": str(uuid.uuid4()),
                "signature": signature,
                "type": "swap",
                "source": "signal" if channel_name else "user",
                "input_token": "SOL",
                "output_token": token_address,
                "input_amount": amount_sol,
                "output_amount": output_amount,
                "status": "success"
            })
            
            # Send notification
            send_notification_task.delay(
                user_id,
                f"✅ <b>Swap Successful!</b>\n\n"
                f"Swapped: {amount_sol} SOL\n"
                f"Received: ~{output_amount:.6f} tokens\n\n"
                f"Signature: <code>{signature}</code>\n\n"
                f"View: https://solscan.io/tx/{signature}"
            )
            
            logger.info(f"[TASK:SWAP] Swap successful: {signature}")
            return {"success": True, "signature": signature}
        else:
            error = result.get("error", "Unknown error")
            logger.error(f"[TASK:SWAP] Swap failed: {error}")
            
            # Send error notification
            send_notification_task.delay(
                user_id,
                f"❌ <b>Swap Failed</b>\n\n"
                f"Error: {error}\n\n"
                f"Please check your balance and try again."
            )
            
            return {"success": False, "error": error}
        
    except Exception as e:
        logger.error(f"[TASK:SWAP] Error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# ==================== NOTIFICATION TASKS ====================

@celery_app.task(queue='low', name='tasks.send_notification_task')
def send_notification_task(user_id: int, message: str, parse_mode: str = "HTML"):
    """
    LOW PRIORITY: Send Telegram notification
    FIXED: Added explicit task name
    """
    try:
        asyncio.run(bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode=parse_mode
        ))
        logger.info(f"[TASK:NOTIFICATION] Sent to user {user_id}")
        return {"success": True}
    except Exception as e:
        logger.error(f"[TASK:NOTIFICATION] Error: {e}")
        return {"success": False, "error": str(e)}


# ==================== HELPER FUNCTIONS ====================

def get_active_call(twilio_call_sid: str):
    """Get active call data from Redis"""
    try:
        redis_key = f"active_call:{twilio_call_sid}"
        call_data_json = redis_client.get(redis_key)
        
        if call_data_json:
            call_data = json.loads(call_data_json)
            logger.info(f"[HELPER] Found call data for {twilio_call_sid}")
            return call_data
        else:
            logger.warning(f"[HELPER] No call data found for {twilio_call_sid}")
            return None
    except Exception as e:
        logger.error(f"[HELPER] Error getting call data: {e}")
        return None

def remove_active_call(twilio_call_sid: str):
    """Remove call from Redis"""
    try:
        redis_key = f"active_call:{twilio_call_sid}"
        redis_client.delete(redis_key)
        logger.info(f"[HELPER] Removed call data for {twilio_call_sid}")
    except Exception as e:
        logger.error(f"[HELPER] Error removing call data: {e}")

# Log that tasks module is loaded
logger.info("[TASKS] ✅ Tasks module loaded successfully")
logger.info(f"[TASKS] Registered tasks: {list(celery_app.tasks.keys())}")