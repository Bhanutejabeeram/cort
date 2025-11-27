"""
Cortex Unified Bot - Bot Handlers (Part 1)
Handles all Telegram command and message interactions
"""

import logging
import uuid
import re
import html
import base58
from datetime import datetime
from typing import Dict, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

import random
import json
import redis
from config import REDIS_URL, VERIFICATION_CODE_EXPIRY

import hashlib
from services.alchemy_transfer import alchemy_transfer
from config import BASE_TRANSACTION_FEE

from services.wallet_manager import WalletManager
from services.jupiter_swap import JupiterAPI
import monitoring.channel_monitor as monitor_module

# Initialize Redis for verification codes
redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    ssl_cert_reqs=None  
)

logger = logging.getLogger(__name__)

# Conversation states
IMPORT_METHOD, IMPORT_DATA = range(2)

# Global stores for pending operations
pending_swaps = {}
pending_payments = {}
pending_signal_swaps = {}
pending_verifications = {}


class BotHandlers:
    """Handles all bot interactions"""
    
    def __init__(self, database, ai_handler):
        """Initialize handlers"""
        self.db = database
        self.ai = ai_handler
        self.wallet_manager = WalletManager()
        self.jupiter = JupiterAPI()
        
        logger.info("Bot handlers initialized")

    def _generate_verification_code(self) -> str:
        """Generate 4-digit verification code"""
        return str(random.randint(1000, 9999))


    def _mask_phone_number(self, phone_number: str) -> str:
        """Mask phone number for display: +91XXXXXX7890"""
        if len(phone_number) <= 6:
            return phone_number
        
        country_code_end = 3 if len(phone_number) > 12 else 2
        return phone_number[:country_code_end] + 'X' * (len(phone_number) - country_code_end - 4) + phone_number[-4:]


    async def start_phone_verification(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                        phone_number: str, telegram_id: int):
        """Start phone verification process"""
        try:
            # Get username
            user = self.db.get_user(telegram_id)
            username = user.get("username", "user") if user else "user"
            
            # Generate verification code
            code = self._generate_verification_code()
            
            # Store in Redis with expiry
            verify_key = f"phone_verify:{telegram_id}"
            verify_data = {
                "code": code,
                "phone_number": phone_number,
                "username": username,
                "telegram_id": telegram_id
            }
            
            redis_client.setex(
                verify_key,
                VERIFICATION_CODE_EXPIRY,
                json.dumps(verify_data)
            )
            
            logger.info(f"[VERIFY] Generated code {code} for user {telegram_id}")
            
            # Create verification message with buttons
            masked_phone = self._mask_phone_number(phone_number)
            
            message = (
                f"<b>Phone Verification</b>\n\n"
                f"Phone: {masked_phone}\n\n"
                f"Your verification code is:\n\n"
                f"<code>{code}</code>\n\n"
                f"You will receive a call from Cortexa.\n"
                f"Enter this code when prompted.\n\n"
                f"Code expires in 10 minutes."
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("Get Call", callback_data=f"verify_call_{telegram_id}"),
                    InlineKeyboardButton("Regenerate", callback_data=f"verify_regen_{telegram_id}")
                ],
                [
                    InlineKeyboardButton("Cancel", callback_data=f"verify_cancel_{telegram_id}")
                ]
            ]
            
            await update.message.reply_text(
                message,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            logger.info(f"[VERIFY] Sent verification message to user {telegram_id}")
            
        except Exception as e:
            logger.error(f"[VERIFY] Start verification error: {e}", exc_info=True)
            await update.message.reply_text("Failed to start verification. Please try again.")


    async def verification_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle verification button callbacks"""
        try:
            query = update.callback_query
            await query.answer()
            
            parts = query.data.split("_")
            action = parts[1]  # call, regen, or cancel
            telegram_id = int(parts[2])
            
            # Verify the clicker is the requester
            if query.from_user.id != telegram_id:
                await query.answer("This verification is for another user", show_alert=True)
                return
            
            # Get verification data from Redis
            verify_key = f"phone_verify:{telegram_id}"
            verify_data_json = redis_client.get(verify_key)
            
            if action == "cancel":
                # Cancel verification
                if verify_data_json:
                    redis_client.delete(verify_key)
                await query.edit_message_text("Phone verification cancelled.")
                return
            
            if not verify_data_json:
                await query.edit_message_text(
                    "<b>Verification Expired</b>\n\n"
                    "Please set your phone number again to restart verification.",
                    parse_mode="HTML"
                )
                return
            
            verify_data = json.loads(verify_data_json)
            phone_number = verify_data["phone_number"]
            username = verify_data["username"]
            code = verify_data["code"]
            
            if action == "regen":
                # Regenerate code
                new_code = self._generate_verification_code()
                verify_data["code"] = new_code
                
                redis_client.setex(
                    verify_key,
                    VERIFICATION_CODE_EXPIRY,
                    json.dumps(verify_data)
                )
                
                masked_phone = self._mask_phone_number(phone_number)
                
                message = (
                    f"<b>Phone Verification</b>\n\n"
                    f"Phone: {masked_phone}\n\n"
                    f"Your NEW verification code is:\n\n"
                    f"<code>{new_code}</code>\n\n"
                    f"You will receive a call from Cortexa.\n"
                    f"Enter this code when prompted.\n\n"
                    f"Code expires in 10 minutes."
                )
                
                keyboard = [
                    [
                        InlineKeyboardButton("Get Call", callback_data=f"verify_call_{telegram_id}"),
                        InlineKeyboardButton("Regenerate", callback_data=f"verify_regen_{telegram_id}")
                    ],
                    [
                        InlineKeyboardButton("Cancel", callback_data=f"verify_cancel_{telegram_id}")
                    ]
                ]
                
                await query.edit_message_text(
                    message,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
                logger.info(f"[VERIFY] Regenerated code {new_code} for user {telegram_id}")
                return
            
            if action == "call":
                # Initiate verification call
                await query.edit_message_text(
                    f"<b>Calling...</b>\n\n"
                    f"Your phone should ring shortly.\n"
                    f"Enter code <code>{code}</code> when prompted.",
                    parse_mode="HTML"
                )
                
                # Import Twilio handler
                from services.twilio_calls import TwilioHandler
                twilio_handler = TwilioHandler(self.db)
                
                # Store verification data with call SID mapping
                call_success = await twilio_handler.make_verification_call(
                    phone_number=phone_number,
                    username=username
                )
                
                if call_success:
                    # Store mapping from call SID to verification data
                    call_sid = twilio_handler.last_call_sid
                    
                    call_verify_key = f"verify_call:{call_sid}"
                    redis_client.setex(
                        call_verify_key,
                        VERIFICATION_CODE_EXPIRY,
                        json.dumps(verify_data)
                    )
                    
                    logger.info(f"[VERIFY] Call initiated: {call_sid} for user {telegram_id}")
                    
                    # Start polling for result
                    context.job_queue.run_once(
                        self._check_verification_result,
                        when=5,  # Check after 5 seconds
                        data={
                            "call_sid": call_sid,
                            "telegram_id": telegram_id,
                            "phone_number": phone_number,
                            "chat_id": query.message.chat_id,
                            "message_id": query.message.message_id,
                            "attempts": 0
                        }
                    )
                else:
                    await query.edit_message_text(
                        "<b>Call Failed</b>\n\n"
                        "Could not initiate the verification call.\n"
                        "Please check your phone number and try again.",
                        parse_mode="HTML"
                    )
            
        except Exception as e:
            logger.error(f"[VERIFY] Callback error: {e}", exc_info=True)
            await query.edit_message_text("An error occurred. Please try again.")


    async def _check_verification_result(self, context: ContextTypes.DEFAULT_TYPE):
        """Poll for verification result from webhook"""
        try:
            job_data = context.job.data
            call_sid = job_data["call_sid"]
            telegram_id = job_data["telegram_id"]
            phone_number = job_data["phone_number"]
            chat_id = job_data["chat_id"]
            message_id = job_data["message_id"]
            attempts = job_data["attempts"]
            
            # Check for result in Redis
            result_key = f"verify_result:{call_sid}"
            result_json = redis_client.get(result_key)
            
            if result_json:
                result = json.loads(result_json)
                redis_client.delete(result_key)
                
                # Clean up verification key
                verify_key = f"phone_verify:{telegram_id}"
                redis_client.delete(verify_key)
                
                if result.get("success"):
                    # Verification successful - update database
                    self.db.verify_phone_number(telegram_id)
                    
                    masked_phone = self._mask_phone_number(phone_number)
                    
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=(
                            f"<b>Phone Verified!</b>\n\n"
                            f"Your phone number {masked_phone} was successfully verified.\n\n"
                            f"From now on, you will receive calls when we detect token signals "
                            f"in your monitored channels."
                        ),
                        parse_mode="HTML"
                    )
                    
                    logger.info(f"[VERIFY] Phone verified for user {telegram_id}")
                    
                else:
                    # Verification failed
                    error = result.get("error", "unknown")
                    
                    if error == "incorrect_code":
                        error_msg = "The code you entered was incorrect."
                    elif error.startswith("call_"):
                        status = error.replace("call_", "")
                        error_msg = f"Call {status}. Please try again."
                    else:
                        error_msg = "Verification failed. Please try again."
                    
                    keyboard = [
                        [InlineKeyboardButton("Try Again", callback_data=f"verify_regen_{telegram_id}")]
                    ]
                    
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=(
                            f"<b>Verification Failed</b>\n\n"
                            f"{error_msg}\n\n"
                            f"Click below to regenerate a new code and try again."
                        ),
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    
                    logger.info(f"[VERIFY] Verification failed for user {telegram_id}: {error}")
                
                return
            
            # No result yet - retry if under limit
            if attempts < 30:  # Max 30 attempts = ~60 seconds
                context.job_queue.run_once(
                    self._check_verification_result,
                    when=2,  # Check every 2 seconds
                    data={
                        **job_data,
                        "attempts": attempts + 1
                    }
                )
            else:
                # Timeout
                keyboard = [
                    [InlineKeyboardButton("Try Again", callback_data=f"verify_call_{telegram_id}")]
                ]
                
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        "<b>Verification Timeout</b>\n\n"
                        "We didn't receive your code input.\n"
                        "Click below to request another call."
                    ),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
                logger.info(f"[VERIFY] Timeout for user {telegram_id}")
            
        except Exception as e:
            logger.error(f"[VERIFY] Check result error: {e}", exc_info=True)

    def _format_channel_info(self, result: dict) -> str:
        """
        Format channel information for display with production-level null handling
        
        Args:
            result: Dictionary from channel_monitor.add_channel_monitoring()
            
        Returns:
            Formatted HTML string ready for Telegram
            
        Handles:
            - None values safely
            - Private channels (no member count)
            - Empty channels (0 members)
            - Large numbers with comma formatting
        """
        channel_username = result.get('channel_username', 'Unknown')
        channel_title = result.get('channel_title', 'Unknown Channel')
        members_count = result.get('members_count')
        is_private = result.get('is_private', False)
        
        # Format members count with proper business logic
        if is_private or members_count is None:
            # Couldn't fetch count (no admin privileges or private channel)
            members_display = "<i>Private (member count hidden)</i>"
        elif members_count == 0:
            # Channel exists but has no members yet
            members_display = "<b>No members yet</b>"
        elif members_count < 100:
            # Small channel - show exact count
            members_display = f"<b>{members_count}</b> members"
        elif members_count < 1000:
            # Medium channel - show exact count
            members_display = f"<b>{members_count}</b> members"
        else:
            # Large channel - format with commas for readability
            members_display = f"<b>{members_count:,}</b> members"
        
        return (
            f"<b>Now Monitoring Channel</b>\n\n"
            f"<b>Channel:</b> @{channel_username}\n"
            f"<b>Title:</b> {channel_title}\n"
            f"{members_display}\n\n"
            f"<i>I'll send you alerts when buy signals are detected!</i>"
        )
    
    def _format_route(self, quote: dict) -> str:
        """Format swap route from Jupiter quote"""
        try:
            route_plan = quote.get("routePlan", [])
            if not route_plan:
                return "Direct swap"
            
            # Extract DEX names from route
            dex_names = []
            for step in route_plan:
                swap_info = step.get("swapInfo", {})
                label = swap_info.get("label", "Unknown")
                if label and label not in dex_names:
                    dex_names.append(label)
            
            if dex_names:
                return " → ".join(dex_names[:3])  # Show max 3 DEXs
            else:
                return "Direct swap"
        except:
            return "Direct swap"
    
    # ==================== COMMAND HANDLERS ====================
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        try:
            user = update.effective_user
            telegram_id = user.id
            username = (user.username or user.first_name or "User").lstrip('@').lower()
            
            claim_result = self.db.claim_pending_wallet(telegram_id, username)
            
            if claim_result.get("success"):
                wallet_address = claim_result["wallet_address"]
                private_key = claim_result["private_key"]
                notifications = claim_result.get("notifications", [])
                
                await update.message.reply_text(
                    f"Hey {html.escape(username)}, welcome to Cortex.\n\n"
                    f"Someone sent you a payment, so I've set up a wallet for you.\n\n"
                    f"<b>Wallet Address</b>\n"
                    f"<code>{wallet_address}</code>\n\n"
                    f"<b>Private Key</b>\n"
                    f"<code>{private_key}</code>\n\n"
                    f"Make sure to save your private key somewhere safe. "
                    f"I won't be able to recover it for you, and you should never share it with anyone.",
                    parse_mode="HTML"
                )
                
                for notif in notifications:
                    if notif.get("type") == "payment_received":
                        await update.message.reply_text(
                        f"You received a payment.\n\n"
                        f"<b>Amount:</b> {notif['amount']} {notif['token']}\n"
                        f"<b>From:</b> @{html.escape(notif['sender_username'])}\n\n"
                        f"<b>Transaction</b>\n"
                        f"<code>{notif['signature']}</code>\n\n"
                        f"https://solscan.io/tx/{notif['signature']}",
                        parse_mode="HTML"
                    )
                
                return
            
            db_user = self.db.get_user(telegram_id)
            
            if not db_user:
                self.db.create_user(telegram_id, username)
                db_user = self.db.get_user(telegram_id)
            
            has_wallet = self.db.user_has_wallet(telegram_id)
            
            if not has_wallet:
                await update.message.reply_text(
                    f"Hey {html.escape(username)}, welcome to Cortex.\n\n"
                    f"I'm your personal Solana assistant. I can help you trade tokens, "
                    f"track KOL signals, send payments, and more through natural conversation.\n\n"
                    f"To get started, let's set up your wallet:\n\n"
                    f"/createwallet  -  Generate a fresh wallet\n"
                    f"/importwallet  -  Use an existing wallet\n\n"
                    f"Once that's done, just type what you need.",
                    parse_mode="HTML"
                )
            else:
                wallet_address = db_user["wallet_address"]
                stats = self.db.get_user_statistics(telegram_id)
                
                await update.message.reply_text(
                    f"Welcome back, {html.escape(username)}.\n\n"
                    f"<b>Your Wallet</b>\n"
                    f"<code>{wallet_address}</code>\n\n"
                    f"<b>Stats</b>\n"
                    f"Swaps: {stats['total_swaps']}  |  "
                    f"Volume: {stats['total_volume_sol']:.2f} SOL  |  "
                    f"Channels: {stats['active_channels']}\n\n"
                    f"What would you like to do? Just ask me anything like "
                    f"\"check my balance\" or \"swap 1 SOL for USDC\".",
                    parse_mode="HTML"
                )
        
        except Exception as e:
            logger.error(f"Start command error: {e}", exc_info=True)
            await update.message.reply_text("An error occurred. Please try again.")
    
    async def create_wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /createwallet command"""
        try:
            telegram_id = update.effective_user.id
            
            if self.db.user_has_wallet(telegram_id):
                await update.message.reply_text(
                    "You already have a wallet connected.\n\n"
                    "If you'd like to use a different one, you can run /importwallet to replace it. "
                    "Just make sure you've saved your current private key first.",
                    parse_mode="HTML"
                )
                return
            
            username = update.effective_user.username or update.effective_user.first_name
            claim_result = self.db.claim_pending_wallet(telegram_id, username)
            
            if claim_result.get("success"):
                await update.message.reply_text(
                    f"You already have a wallet that was created for you.\n\n"
                    f"<b>Wallet Address</b>\n"
                    f"<code>{claim_result['wallet_address']}</code>\n\n"
                    f"<b>Private Key</b>\n"
                    f"<code>{claim_result['private_key']}</code>\n\n"
                    f"Keep your private key safe and never share it with anyone.",
                    parse_mode="HTML"
                )
                return
            
            await update.message.reply_text("Creating wallet...")
            
            wallet_data = self.wallet_manager.create_new_wallet()
            
            if wallet_data.get("success"):
                # Ensure user exists in database before saving wallet
                db_user = self.db.get_user(telegram_id)
                if not db_user:
                    username = (update.effective_user.username or update.effective_user.first_name or "User").lstrip('@').lower()
                    self.db.create_user(telegram_id, username)
                    logger.info(f"Created user {telegram_id} during wallet creation")
                
                success = self.db.save_wallet(
                    telegram_id,
                    wallet_data["address"],
                    wallet_data["private_key"],
                    "created"
                )
                
                if success:
                    await update.message.reply_text(
                        f"Your wallet is ready.\n\n"
                        f"<b>Wallet Address</b>\n"
                        f"<code>{wallet_data['address']}</code>\n\n"
                        f"<b>Private Key</b>\n"
                        f"<code>{wallet_data['private_key']}</code>\n\n"
                        f"Save your private key now. I can't recover it for you, "
                        f"and you should never share it with anyone.\n\n"
                        f"You're all set. Just tell me what you need, "
                        f"like \"check my balance\" or \"what's the price of BONK\".",
                        parse_mode="HTML"
                    )
                else:
                    await update.message.reply_text(
                        "There was an error saving your wallet. Please try again.",
                        parse_mode="HTML"
                    )
            else:
                await update.message.reply_text(
                    f"Error creating wallet: {wallet_data.get('error')}",
                    parse_mode="HTML"
                )
        
        except Exception as e:
            logger.error(f"Create wallet error: {e}", exc_info=True)
            await update.message.reply_text("Failed to create wallet")
    
    # ==================== IMPORT WALLET CONVERSATION ====================
    
    async def import_wallet_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start import wallet conversation"""
        telegram_id = update.effective_user.id
        
        if self.db.user_has_wallet(telegram_id):
            await update.message.reply_text(
                "You already have a wallet connected. "
                "Importing a new one will replace it.\n\n"
                "Make sure you've saved your current private key before continuing.",
                parse_mode="HTML"
            )

        keyboard = [
            [InlineKeyboardButton("Private Key", callback_data="import_private_key")],
            [InlineKeyboardButton("Recovery Phrase", callback_data="import_mnemonic")],
            [InlineKeyboardButton("Cancel", callback_data="import_cancel")]
        ]

        await update.message.reply_text(
            "How would you like to import your wallet?\n\n"
            "Choose an option below.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        
        return IMPORT_METHOD
    
    async def import_method_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle import method selection"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "import_cancel":
            await query.edit_message_text(
                "Import cancelled. Let me know if you need anything else.",
                parse_mode="HTML"
            )
            return ConversationHandler.END

        if query.data == "import_private_key":
            await query.edit_message_text(
                "Send me your private key in the next message.\n\n"
                "Make sure no one else can see your screen. "
                "Your message will be deleted automatically for security.",
                parse_mode="HTML"
            )
            context.user_data["import_method"] = "private_key"

        elif query.data == "import_mnemonic":
            await query.edit_message_text(
                "Send me your 12 or 24 word recovery phrase in the next message.\n\n"
                "Make sure no one else can see your screen. "
                "Your message will be deleted automatically for security.",
                parse_mode="HTML"
            )
            context.user_data["import_method"] = "mnemonic"
        
        return IMPORT_DATA
    
    async def import_data_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle wallet import data"""
        try:
            user = update.effective_user
            telegram_id = user.id
            username = (user.username or user.first_name or "User").lstrip('@').lower()
            import_method = context.user_data.get("import_method")
            data = update.message.text.strip()
            
            try:
                await update.message.delete()
            except:
                pass
            
            if import_method == "private_key":
                result = self.wallet_manager.import_from_private_key(data)
            elif import_method == "mnemonic":
                result = self.wallet_manager.import_from_mnemonic(data)
            else:
                await update.message.reply_text(
                    "Invalid import method. Please try again with /importwallet",
                    parse_mode="HTML"
                )
                return ConversationHandler.END
            
            if result.get("success"):
                # Ensure user exists in database before saving wallet
                db_user = self.db.get_user(telegram_id)
                if not db_user:
                    self.db.create_user(telegram_id, username)
                    logger.info(f"Created user {telegram_id} during wallet import")
                
                success = self.db.save_wallet(
                    telegram_id,
                    result["address"],
                    result["private_key"],
                    "imported"
                )
                
                if success:
                    await update.message.reply_text(
                        f"Wallet imported successfully.\n\n"
                        f"<b>Wallet Address</b>\n"
                        f"<code>{result['address']}</code>\n\n"
                        f"You're all set. Just tell me what you need.",
                        parse_mode="HTML"
                    )
                else:
                    await update.message.reply_text(
                        "There was an error saving your wallet. Please try again.",
                        parse_mode="HTML"
                    )
                    logger.error(f"Failed to save wallet for user {telegram_id}")
            else:
                await update.message.reply_text(
                        f"Import failed: {result.get('error')}",
                        parse_mode="HTML"
                    )            
            return ConversationHandler.END
        
        except Exception as e:
            logger.error(f"Import data error: {e}", exc_info=True)
            await update.message.reply_text(
                "Import failed. Please try again with /importwallet",
                parse_mode="HTML"
            )
            return ConversationHandler.END
    
    async def import_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel import"""
        await update.message.reply_text(
            "Import cancelled. Let me know if you need anything else.",
            parse_mode="HTML"
        )
        return ConversationHandler.END
    
    async def debug_channel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Debug: Show monitored channels and their IDs"""
        try:
            telegram_id = update.effective_user.id
            
            # Get channels from database
            channels_db = self.db.get_active_channels(telegram_id)
            
            if not channels_db:
                await update.message.reply_text(
                    "No channels monitored yet.",
                    parse_mode="HTML"
                )
                return
            
            debug_msg = "<b>Channel Debug Info</b>\n\n"
            
            for ch in channels_db:
                debug_msg += f"<b>Channel:</b> @{ch['channel_username']}\n"
                debug_msg += f"<b>DB Channel ID:</b> <code>{ch['channel_id']}</code>\n"
                debug_msg += f"<b>Calls Enabled:</b> {ch.get('calls_enabled', True)}\n"
                debug_msg += f"<b>Added At:</b> {ch.get('added_at')}\n\n"
            
            # Get from monitor
            import monitoring.channel_monitor as monitor_module
            if monitor_module.channel_monitor_instance:
                monitor = monitor_module.channel_monitor_instance
                debug_msg += f"<b>Monitor Status:</b>Running\n"
                debug_msg += f"<b>Active Channels in Monitor:</b>\n"
                debug_msg += f"<code>{dict(monitor.active_channels)}</code>\n"
            else:
                debug_msg += f"<b>Monitor Status:</b> Not Running\n"
            
            await update.message.reply_text(debug_msg, parse_mode="HTML")
            
        except Exception as e:
            logger.error(f"Debug channel error: {e}", exc_info=True)
            await update.message.reply_text(f"Error: {e}")
    
    async def handle_ai_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all non-command messages with AI"""
        thinking_msg = None  # Track thinking message for cleanup
        
        try:
            telegram_id = update.effective_user.id
            user_message = update.message.text
            user = update.effective_user
            
            # Check if user has wallet first
            if not self.db.user_has_wallet(telegram_id):
                await update.message.reply_text(
                    "You'll need a wallet before we can get started.\n\n"
                    "/createwallet  -  Generate a fresh wallet\n"
                    "/importwallet  -  Use an existing wallet",
                    parse_mode="HTML"
                )
                return
            
            # Check if in group chat
            is_group = update.effective_chat.type in ['group', 'supergroup']

            # In groups, only respond if mentioned or replied to
            if is_group:
                bot_username = context.bot.username
                is_reply_to_bot = (
                    update.message.reply_to_message and
                    update.message.reply_to_message.from_user.id == context.bot.id
                )
                is_mentioned = f"@{bot_username}" in user_message
                
                if not (is_reply_to_bot or is_mentioned):
                    return  # Don't respond in groups unless mentioned
                
                # Add context for group messages
                if update.message.reply_to_message:
                    replied_text = update.message.reply_to_message.text[:200]
                    user_message = f"[CONTEXT: Group Chat - Reply to: {replied_text}]\n\n{user_message}"
                else:
                    user_message = f"[CONTEXT: Group Chat]\n\n{user_message}"
            else:
                # Add context for direct messages
                user_message = f"[CONTEXT: Direct Message]\n\n{user_message}"
            
            # Send "thinking" message instead of just typing indicator
            thinking_msg = await update.message.reply_text("Cortexa thinking...")
            
            # Get user's previous context
            db_user = self.db.get_user(telegram_id)
            previous_response_id = db_user.get("previous_response_id") if db_user else None
            
            logger.info(f"[AI] User {telegram_id} | Previous Response ID: {previous_response_id}")

            # Disable conversation memory in groups
            if is_group:
                previous_response_id = None  # Each group message is independent
                logger.info(f"[AI] Group message - conversation memory disabled")
            else:
                logger.info(f"[AI] User {telegram_id} | Previous Response ID: {previous_response_id}")

            # Call AI
            response_text, new_response_id, tool_result = self.ai.call_ai(
                user_message,
                telegram_id,
                previous_response_id
            )
            
            logger.info(f"[AI] User {telegram_id} | New Response ID: {new_response_id}")
            
            # Save new_response_id to database for next conversation
            if new_response_id and not is_group:
                self.db.update_user_activity(telegram_id, new_response_id)
                logger.info(f"[AI] Saved new response_id to database for user {telegram_id}")
            
            # Format response for Telegram
            response_text = self._format_telegram_response(response_text)
            
            # Delete thinking message before sending actual response
            if thinking_msg:
                try:
                    await thinking_msg.delete()
                    thinking_msg = None  # Mark as deleted
                except Exception as del_error:
                    logger.warning(f"[AI] Could not delete thinking message: {del_error}")
            
            # Handle special tool results
            if tool_result:
                action = tool_result.get("action")

                # The _handle_tool_result will send the detailed response
                if action in ["add_channel", "remove_channel"]:
                    await self._handle_tool_result(update, context, tool_result, None, telegram_id)
                    return  
                
                # Check if it's a payment quote
                if tool_result.get("payment_data") and tool_result.get("display"):
                    payment_id = str(uuid.uuid4())[:8]
                    
                    pending_payments[payment_id] = {
                        "telegram_id": telegram_id,
                        "payment_data": tool_result["payment_data"],
                        "timestamp": datetime.now()
                    }
                    
                    keyboard = [
                        [
                            InlineKeyboardButton(" Confirm ", callback_data=f"pay_confirm_{payment_id}"),
                            InlineKeyboardButton(" Cancel ", callback_data=f"pay_cancel_{payment_id}")
                        ]
                    ]
                    
                    await update.message.reply_text(
                        response_text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    await self._handle_tool_result(update, context, tool_result, response_text, telegram_id)
            else:
                # Send regular response
                await update.message.reply_text(
                    response_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
        
        except Exception as e:
            logger.error(f"AI message handler error: {e}", exc_info=True)
            
            # Clean up thinking message on error
            if thinking_msg:
                try:
                    await thinking_msg.delete()
                except:
                    pass
            
            await update.message.reply_text("An error occurred. Please try again.")
    

    
    async def _handle_tool_result(self, update, context, tool_result, response_text, telegram_id):
        """Handle special tool results that need additional UI"""
        try:
            action = tool_result.get("action")

            if action == "start_phone_verification":
                phone_number = tool_result.get("phone_number")
                await self.start_phone_verification(update, context, phone_number, telegram_id)
                return
            
            # ADD CHANNEL - Execute in main process
            if action == "add_channel":
                channel_username = tool_result["channel"]
                
                # Show loading message
                loading_msg = await update.message.reply_text(
                    f"Adding @{channel_username} to monitoring...",
                    parse_mode="HTML"
                )
                
                # Execute in main process (where Telethon is connected)
                if monitor_module.channel_monitor_instance:
                    result = await monitor_module.channel_monitor_instance.add_channel_monitoring(
                        telegram_id, 
                        channel_username
                    )
                    
                    if result.get("success"):
                        # Save to database
                        channel_id = result.get("channel_id")
                        self.db.add_channel_monitoring(telegram_id, channel_username, channel_id)
                        
                        success_message = self._format_channel_info(result)
                        
                        await loading_msg.edit_text(
                            success_message,
                            parse_mode="HTML"
                        )
                    else:
                        error_msg = result.get('error', 'Unknown error')
                        
                        await loading_msg.edit_text(
                            f"<b>Failed to Monitor Channel</b>\n\n"
                            f"Channel: @{channel_username}\n"
                            f"Reason: {error_msg}\n\n"
                            f"<b>Troubleshooting:</b>\n"
                            f"Make sure the channel is public\n"
                            f"Check the username is correct\n"
                            f"Verify the channel allows bot members",
                            parse_mode="HTML"
                        )
                else:
                    await loading_msg.edit_text(
                        "<b>Channel Monitoring Not Available</b>\n\n"
                        "The monitoring service is not running.",
                        parse_mode="HTML"
                    )
                return
            
            # REMOVE CHANNEL - Execute in main process
            elif action == "remove_channel":
                channel_username = tool_result["channel"]
                
                loading_msg = await update.message.reply_text(
                    f"Removing @{channel_username}...",
                    parse_mode="HTML"
                )
                
                if monitor_module.channel_monitor_instance:
                    success = await monitor_module.channel_monitor_instance.remove_channel_monitoring(
                        telegram_id,
                        channel_username
                    )
                    
                    if success:
                        self.db.remove_channel_monitoring(telegram_id, channel_username)
                        await loading_msg.edit_text(
                            f"Stopped monitoring @{channel_username}",
                            parse_mode="HTML"
                        )
                    else:
                        await loading_msg.edit_text(
                            f"Failed to remove @{channel_username}",
                            parse_mode="HTML"
                        )
                else:
                    await loading_msg.edit_text(
                        "Channel monitoring not available",
                        parse_mode="HTML"
                    )
                return
            
            # Swap preview - needs confirmation
            if action == "swap_preview" and tool_result.get("needs_confirmation"):
                swap_id = str(uuid.uuid4())[:8]
                pending_swaps[swap_id] = {
                    "telegram_id": telegram_id,
                    "input_token": tool_result["input_token"],
                    "output_token": tool_result["output_token"],
                    "amount": tool_result["amount"],
                    "output_amount": tool_result.get("output_amount"),
                    "slippage_bps": tool_result.get("slippage_bps", 500),
                    "timestamp": datetime.now()
                }
                
                # Get token info using existing _search_token function
                input_token_addr = tool_result["input_token"]
                output_token_addr = tool_result["output_token"]
                
                # Input token info
                if input_token_addr == "So11111111111111111111111111111111111111112":
                    input_display = "SOL"
                    input_name = "Solana"
                    input_mcap = "N/A"
                else:
                    input_result = self.ai._search_token(input_token_addr)
                    if input_result.get("success"):
                        input_data = input_result.get("token_data", {})
                        input_display = input_data.get("symbol", input_token_addr[:8] + "...")
                        input_name = input_data.get("name", "Unknown")
                        input_mcap = input_data.get("market_cap_formatted", "N/A")
                    else:
                        input_display = input_token_addr[:8] + "..."
                        input_name = "Unknown Token"
                        input_mcap = "N/A"
                
                # Output token info
                if output_token_addr == "So11111111111111111111111111111111111111112":
                    output_display = "SOL"
                    output_name = "Solana"
                    output_mcap = "N/A"
                else:
                    output_result = self.ai._search_token(output_token_addr)
                    if output_result.get("success"):
                        output_data = output_result.get("token_data", {})
                        output_display = output_data.get("symbol", output_token_addr[:8] + "...")
                        output_name = output_data.get("name", "Unknown")
                        output_mcap = output_data.get("market_cap_formatted", "N/A")
                    else:
                        output_display = output_token_addr[:8] + "..."
                        output_name = "Unknown Token"
                        output_mcap = "N/A"
                
                # Convert values to proper types
                try:
                    output_amount = float(tool_result.get('output_amount', 0))
                    price_impact = float(tool_result.get('price_impact', 0))
                    slippage_bps = int(tool_result.get('slippage_bps', 500))
                except (ValueError, TypeError):
                    output_amount = 0
                    price_impact = 0
                    slippage_bps = 500
                
                preview_msg = (
                    f"<b>Swap Preview</b>\n\n"
                    f"<b>SELL</b>\n"
                    f"{input_name} / {input_display}\n"
                    f"Amount: {tool_result['amount']} {input_display}\n\n"
                    f"<b>BUY</b>\n"
                    f"{output_name} / {output_display}\n"
                    f"Address: <code>{output_token_addr}</code>\n"
                    f"Est. Receive: ~{output_amount:,.2f} {output_display}\n\n"
                    f"<b>Route:</b> {self._format_route(tool_result.get('quote', {}))}\n"
                    f"<b>Price Impact:</b> {price_impact:.2f}%\n"
                    f"<b>Slippage:</b> {slippage_bps / 100:.1f}%\n\n"
                    f"Click Confirm to execute this swap."
                )
                
                keyboard = [
                    [
                        InlineKeyboardButton(" Confirm ", callback_data=f"swap_confirm_{swap_id}"),
                        InlineKeyboardButton(" Cancel ", callback_data=f"swap_cancel_{swap_id}")
                    ]
                ]
                
                await update.message.reply_text(
                    preview_msg,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    disable_web_page_preview=True
                )
                return
            
            # Payment preview - needs confirmation
            elif action == "payment_preview" and tool_result.get("needs_confirmation"):
                payment_id = str(uuid.uuid4())[:8]
                pending_payments[payment_id] = {
                    "telegram_id": telegram_id,
                    "recipient": tool_result["recipient"],
                    "amount": tool_result["amount"],
                    "token": tool_result["token"],
                    "timestamp": datetime.now()
                }
                
                keyboard = [
                    [
                        InlineKeyboardButton(" Send ", callback_data=f"pay_confirm_{payment_id}"),
                        InlineKeyboardButton(" Cancel ", callback_data=f"pay_cancel_{payment_id}")
                    ]
                ]
                
                await update.message.reply_text(
                    response_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            
            # Regular response
            else:
                await update.message.reply_text(
                    response_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
        
        except Exception as e:
            logger.error(f"[BOT_HANDLERS] Tool result error: {e}", exc_info=True)
            
            try:
                # Build context-aware error message
                error_message = (
                    "<b>Something Went Wrong</b>\n\n"
                    "I encountered an error while processing your request.\n\n"
                    
                    "Contact support if the issue persists"
                )
                
                # Only send response_text if it's valid
                if response_text and isinstance(response_text, str) and response_text.strip():
                    await update.message.reply_text(response_text, parse_mode="HTML")
                else:
                    # Fallback to error message
                    await update.message.reply_text(error_message, parse_mode="HTML")
                    
            except Exception as inner_e:
                # Last resort: log and fail silently
                # Don't let error handler itself crash the bot
                logger.error(f"[BOT_HANDLERS] Failed to send error message: {inner_e}", exc_info=True)

    # ==================== SIGNAL HANDLING (FROM PART 1) ====================
    
    async def handle_signal_detected(self, signal_data: Dict):
        """Handle detected trading signal from channel monitoring"""
        try:
            user_id = signal_data["user_id"]
            token_address = signal_data["token_address"]
            token_data = signal_data["token_data"]
            channel_name = signal_data["channel_name"]
            
            # Generate signal ID
            signal_id = str(uuid.uuid4())[:8]
            
            # Store signal for button handling
            pending_signal_swaps[signal_id] = {
                "user_id": user_id,
                "token_address": token_address,
                "token_data": token_data,
                "channel_name": channel_name,
                "timestamp": datetime.now()
            }
            
            # Add to signal history
            self.db.add_signal_to_history(user_id, {
                "signal_id": signal_id,
                "channel_username": channel_name,
                "token_address": token_address,
                "token_name": token_data.get("name"),
                "token_symbol": token_data.get("symbol"),
                "classification": "BUY",
                "confidence": signal_data.get("confidence", 0.9),
                "call_made": signal_data.get("call_made", False)
            })
            
            # Create message
            message = (
                f"<b>BUY SIGNAL DETECTED</b>\n\n"
                f"Channel: @{channel_name}\n"
                f"Token: {token_data.get('name')} ({token_data.get('symbol')})\n"
                f"Address: <code>{token_address[:8]}...{token_address[-6:]}</code>\n\n"
                f"Price: ${token_data.get('usdPrice', 0):.8f}\n"
                f"Market Cap: ${token_data.get('mcap', 0):,.0f}\n"
                f"24h Change: {token_data.get('priceChange24h', 0):.2f}%\n\n"
                f"Choose swap amount:"
            )
            
            # Create buttons
            keyboard = [
                [
                    InlineKeyboardButton("0.1 SOL", callback_data=f"signal_swap_{signal_id}_0.1"),
                    InlineKeyboardButton("0.5 SOL", callback_data=f"signal_swap_{signal_id}_0.5"),
                    InlineKeyboardButton("1 SOL", callback_data=f"signal_swap_{signal_id}_1")
                ],
                [
                    InlineKeyboardButton("2 SOL", callback_data=f"signal_swap_{signal_id}_2"),
                    InlineKeyboardButton("5 SOL", callback_data=f"signal_swap_{signal_id}_5"),
                    InlineKeyboardButton("Custom", callback_data=f"signal_swap_{signal_id}_custom")
                ],
                [
                    InlineKeyboardButton("Skip", callback_data=f"signal_swap_{signal_id}_skip")
                ]
            ]
            
            # Send DM to user
            from telegram import Bot
            from config import TELEGRAM_BOT_TOKEN
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            
            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True
            )
            
            logger.info(f"Signal DM sent to user {user_id} for {token_data.get('symbol')}")
        
        except Exception as e:
            logger.error(f"Signal handler error: {e}", exc_info=True)
    
    # ==================== CALLBACK HANDLERS ====================
    
    async def signal_swap_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle signal swap button clicks"""
        try:
            query = update.callback_query
            await query.answer()
            
            parts = query.data.split("_")
            signal_id = parts[2]
            action = parts[3] if len(parts) > 3 else "skip"
            
            # Get signal data
            signal_data = pending_signal_swaps.get(signal_id)
            if not signal_data:
                await query.edit_message_text("Signal expired")
                return
            
            if query.from_user.id != signal_data["user_id"]:
                await query.answer(
                    "This signal swap was for another user",
                    show_alert=True
                )
                return
            
            # Handle skip
            if action == "skip":
                await query.edit_message_text("Skipped. Waiting for next signal.")
                del pending_signal_swaps[signal_id]
                return
            
            # Handle custom amount
            if action == "custom":
                await query.edit_message_text(
                    "Please type the amount of SOL you want to swap:\n"
                    "Example: 3.5"
                )
                # Store context for next message
                context.user_data["pending_signal_swap"] = signal_id
                return
            
            # Parse amount
            try:
                amount = float(action)
            except:
                await query.edit_message_text("Invalid amount")
                return
            
            # Execute swap
            await self._execute_signal_swap(query, signal_id, signal_data, amount)
        
        except Exception as e:
            logger.error(f"Signal swap callback error: {e}")
            await query.edit_message_text("An error occurred")
    
    async def swap_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle AI-driven swap confirmations with fresh quote"""
        query = update.callback_query
        
        try:
            await query.answer()
        except Exception:
            pass
        
        try:
            parts = query.data.split("_")
            action = parts[1]
            swap_id = parts[2]
            
            swap_data = pending_swaps.get(swap_id)
            if not swap_data:
                try:
                    await query.edit_message_text("Swap expired. Please request a new quote.")
                except Exception:
                    await update.effective_chat.send_message("Swap expired. Please request a new quote.")
                return
            
            if query.from_user.id != swap_data["telegram_id"]:
                try:
                    await query.answer("This swap was requested by another user", show_alert=True)
                except Exception:
                    pass
                return
            
            if action == "cancel":
                try:
                    await query.edit_message_text("Swap cancelled")
                except Exception:
                    await update.effective_chat.send_message("Swap cancelled")
                del pending_swaps[swap_id]
                return
            
            is_group = update.effective_chat.type in ['group', 'supergroup']
            logger.info(f"[SWAP EXECUTE] Chat type: {update.effective_chat.type} | is_group: {is_group}")
            
            try:
                await query.edit_message_text("Getting fresh quote and executing swap...")
            except Exception:
                await update.effective_chat.send_message("Getting fresh quote and executing swap...")
            
            telegram_id = swap_data["telegram_id"]
            user = self.db.get_user(telegram_id)
            wallet_address = user["wallet_address"]
            private_key = self.db.get_decrypted_private_key(telegram_id)
            username = user.get("username", "user")
            
            logger.info(f"[SWAP EXECUTE] Starting swap for user {telegram_id}")
            logger.info(f"[SWAP EXECUTE] Input: {swap_data['amount']} {swap_data['input_token'][:8]}...")
            
            result = await self.jupiter.execute_swap(
                wallet_address=wallet_address,
                private_key=private_key,
                input_token=swap_data["input_token"],
                output_token=swap_data["output_token"],
                amount=swap_data["amount"],
                slippage_percent=swap_data.get("slippage_bps", 500) / 100
            )
            
            if result.get("success"):
                signature = result["signature"]
                output_amount = result.get("output_amount", "N/A")
                
                logger.info(f"[SWAP EXECUTE] Swap successful! Signature: {signature}")
                
                self.db.add_transaction(telegram_id, {
                    "tx_id": str(uuid.uuid4()),
                    "signature": signature,
                    "type": "swap",
                    "source": "user",
                    "input_token": swap_data["input_token"],
                    "output_token": swap_data["output_token"],
                    "input_symbol": swap_data.get("input_symbol", ""),
                    "output_symbol": swap_data.get("output_symbol", ""),
                    "input_amount": swap_data["amount"],
                    "output_amount": output_amount,
                    "status": "success"
                })
                
                input_display = swap_data.get("input_symbol") or ("SOL" if swap_data["input_token"] == "So11111111111111111111111111111111111111112" else f"{swap_data['input_token'][:8]}...")
                output_display = swap_data.get("output_symbol") or ("SOL" if swap_data["output_token"] == "So11111111111111111111111111111111111111112" else f"{swap_data['output_token'][:8]}...")
                
                if is_group:
                    logger.info(f"[SWAP EXECUTE] Group chat detected - sending short message + DM")
                    
                    group_message = (
                        f"<b>Swap Executed</b>\n\n"
                        f"@{username}, your swap is complete.\n\n"
                        f"Check your DMs for transaction details."
                    )
                    
                    try:
                        await query.edit_message_text(group_message, parse_mode="HTML")
                    except Exception:
                        await update.effective_chat.send_message(group_message, parse_mode="HTML")
                    
                    logger.info(f"[SWAP EXECUTE] Short message posted in group")
                    
                    dm_message = (
                        f"<b>Swap Successful</b>\n\n"
                        f"<b>Swapped:</b> {swap_data['amount']} {input_display}\n"
                        f"<b>Received:</b> ~{output_amount} {output_display}\n\n"
                        f"<b>Transaction</b>\n"
                        f"<code>{signature}</code>\n\n"
                        f"https://solscan.io/tx/{signature}"
                    )
                    
                    try:
                        await context.bot.send_message(
                            chat_id=telegram_id,
                            text=dm_message,
                            parse_mode="HTML",
                            disable_web_page_preview=True
                        )
                        logger.info(f"[SWAP EXECUTE] Full details sent to user {telegram_id} in DM")
                    except Exception as e:
                        logger.error(f"[SWAP EXECUTE] Could not send DM: {e}")
                    
                else:
                    logger.info(f"[SWAP EXECUTE] DM chat detected - showing full details inline")
                    
                    success_msg = (
                        f"<b>Swap Successful</b>\n\n"
                        f"<b>Swapped:</b> {swap_data['amount']} {input_display}\n"
                        f"<b>Received:</b> ~{output_amount} {output_display}\n\n"
                        f"<b>Transaction</b>\n"
                        f"<code>{signature}</code>\n\n"
                        f"https://solscan.io/tx/{signature}"
                    )
                    
                    try:
                        sent_message = await query.edit_message_text(
                            success_msg,
                            parse_mode="HTML",
                            disable_web_page_preview=True
                        )
                        
                        try:
                            await context.bot.pin_chat_message(
                                chat_id=update.effective_chat.id,
                                message_id=sent_message.message_id,
                                disable_notification=True
                            )
                            logger.info(f"[SWAP EXECUTE] Success message pinned in DM")
                        except Exception as pin_error:
                            logger.warning(f"[SWAP EXECUTE] Could not pin message: {pin_error}")
                            
                    except Exception:
                        await update.effective_chat.send_message(
                            success_msg,
                            parse_mode="HTML",
                            disable_web_page_preview=True
                        )
            
            else:
                error_msg = result.get('error', 'Unknown error')
                logger.error(f"[SWAP EXECUTE] Swap failed: {error_msg}")
                
                error_message = (
                    f"<b>Swap Failed</b>\n\n"
                    f"Error: {error_msg}\n\n"
                    f"Please try again or check your balance."
                )
                
                try:
                    await query.edit_message_text(error_message, parse_mode="HTML")
                except Exception:
                    await update.effective_chat.send_message(error_message, parse_mode="HTML")
            
            del pending_swaps[swap_id]
        
        except Exception as e:
            logger.error(f"[SWAP EXECUTE] Exception in swap_callback_handler: {e}", exc_info=True)
            try:
                await query.edit_message_text("Swap failed due to an error. Please try again.")
            except Exception:
                await update.effective_chat.send_message("Swap failed due to an error. Please try again.")
    
    async def payment_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle payment confirmations with transaction polling"""
        query = update.callback_query
        
        try:
            await query.answer()
        except Exception:
            pass
        
        try:
            parts = query.data.split("_")
            action = parts[1]
            payment_id = parts[2]
            
            payment_info = pending_payments.get(payment_id)
            if not payment_info:
                try:
                    await query.edit_message_text("Payment expired. Please request a new quote.")
                except Exception:
                    await update.effective_chat.send_message("Payment expired. Please request a new quote.")
                return
            
            if query.from_user.id != payment_info["telegram_id"]:
                try:
                    await query.answer("This payment was requested by another user", show_alert=True)
                except Exception:
                    pass
                return
            
            if action == "cancel":
                try:
                    await query.edit_message_text("Payment cancelled")
                except Exception:
                    await update.effective_chat.send_message("Payment cancelled")
                del pending_payments[payment_id]
                return
            
            try:
                await query.edit_message_text("Processing payment...")
            except Exception:
                await update.effective_chat.send_message("Processing payment...")
            
            sender_telegram_id = payment_info["telegram_id"]
            payment_data = payment_info["payment_data"]
            
            logger.info(f"[PAYMENT] Executing payment for user {sender_telegram_id}")
            
            result = await self.execute_payment_secure(sender_telegram_id, payment_data)
            
            if result["success"]:
                signature = result["signature"]
                
                is_group = update.effective_chat.type in ['group', 'supergroup']
                
                if is_group:
                    group_message = (
                        f"<b>Payment Sent</b>\n\n"
                        f"@{result['recipient_username']}, you received <b>{result['amount']} {result['token']}</b> "
                        f"from @{result['sender_username']}\n\n"
                        f"Check your DMs for transaction details."
                    )
                    try:
                        await query.edit_message_text(group_message, parse_mode="HTML")
                    except Exception:
                        await update.effective_chat.send_message(group_message, parse_mode="HTML")
                    
                    sender_dm_message = (
                        f"<b>Payment Successful</b>\n\n"
                        f"<b>From:</b> @{result['sender_username']}\n"
                        f"<code>{result['sender_wallet'][:12]}...{result['sender_wallet'][-8:]}</code>\n\n"
                        f"<b>To:</b> @{result['recipient_username']}\n"
                        f"<code>{result['recipient_wallet'][:12]}...{result['recipient_wallet'][-8:]}</code>\n\n"
                        f"<b>Amount:</b> {result['amount']} {result['token']}\n"
                        f"<b>Status:</b> Confirmed\n\n"
                        f"<b>Transaction</b>\n"
                        f"<code>{signature}</code>\n\n"
                        f"https://solscan.io/tx/{signature}"
                    )
                    
                    try:
                        await context.bot.send_message(
                            chat_id=sender_telegram_id,
                            text=sender_dm_message,
                            parse_mode="HTML"
                        )
                        logger.info(f"[PAYMENT] Sent details to sender {sender_telegram_id} in DM")
                    except Exception as e:
                        logger.error(f"[PAYMENT] Could not send DM to sender: {e}")
                    
                    await self._send_recipient_notification(result, context)
                    
                else:
                    tx_message = (
                        f"<b>Payment Successful</b>\n\n"
                        f"<b>From:</b> @{result['sender_username']}\n"
                        f"<code>{result['sender_wallet'][:12]}...{result['sender_wallet'][-8:]}</code>\n\n"
                        f"<b>To:</b> @{result['recipient_username']}\n"
                        f"<code>{result['recipient_wallet'][:12]}...{result['recipient_wallet'][-8:]}</code>\n\n"
                        f"<b>Amount:</b> {result['amount']} {result['token']}\n"
                        f"<b>Status:</b> Confirmed\n\n"
                        f"<b>Transaction</b>\n"
                        f"<code>{signature}</code>\n\n"
                        f"https://solscan.io/tx/{signature}"
                    )
                    
                    try:
                        await query.edit_message_text(tx_message, parse_mode="HTML")
                    except Exception:
                        await update.effective_chat.send_message(tx_message, parse_mode="HTML")
                    
                    await self._send_recipient_notification(result, context)
            
            else:
                error_msg = result.get('error', 'Unknown error')
                error_message = (
                    f"<b>Payment Failed</b>\n\n"
                    f"Error: {error_msg}\n\n"
                    f"Please check your balance and try again."
                )
                try:
                    await query.edit_message_text(error_message, parse_mode="HTML")
                except Exception:
                    await update.effective_chat.send_message(error_message, parse_mode="HTML")
            
            del pending_payments[payment_id]
            
        except Exception as e:
            logger.error(f"[PAYMENT] Exception in payment_callback_handler: {e}", exc_info=True)
            try:
                await query.edit_message_text("Payment failed due to an error. Please try again.")
            except Exception:
                await update.effective_chat.send_message("Payment failed due to an error. Please try again.")

    async def execute_payment_secure(self, sender_telegram_id: int, payment_data: Dict) -> Dict:
        """Execute payment with wallet creation and blockchain transfer"""
        try:
            logger.info(f"[PAYMENT EXEC] Starting secure payment execution")
            
            # Step 1: Get sender's private key
            sender_private_key = self.db.get_decrypted_private_key(sender_telegram_id)
            if not sender_private_key:
                return {"success": False, "error": "Wallet not found"}
            
            sender_user = self.db.get_user(sender_telegram_id)
            sender_wallet = sender_user["wallet_address"]
            sender_username = sender_user["username"]
            
            # Extract payment details
            recipient_username = payment_data["recipient_username"]
            recipient_wallet = payment_data["recipient_wallet"]
            recipient_status = payment_data["recipient_status"]
            recipient_telegram_id = payment_data["recipient_telegram_id"]
            amount = payment_data["amount"]
            token = payment_data["token"]
            token_mint = payment_data["token_mint"]
            token_decimals = payment_data["token_decimals"]
            
            logger.info(f"[PAYMENT EXEC] Recipient status: {recipient_status}")

            # Step 2: Double-check recipient balance for active/pending users
            if recipient_status in ["active", "pending_wallet"] and recipient_wallet and token == "SOL":
                logger.info(f"[PAYMENT EXEC] Verifying recipient's blockchain balance...")
                
                recipient_balance = alchemy_transfer.get_sol_balance(recipient_wallet)
                logger.info(f"[PAYMENT EXEC] Recipient has {recipient_balance} SOL on blockchain")
                
                # If recipient has insufficient balance and amount is too small
                if recipient_balance < 0.00089088:
                    if float(amount) < 0.001:
                        return {
                            "success": False,
                            "error": f"Recipient's wallet has insufficient balance ({recipient_balance} SOL). Minimum transfer is 0.001 SOL to cover rent-exemption. Please try again with a higher amount."
                        }
                    logger.info(f"[PAYMENT EXEC] Amount is sufficient to cover rent-exemption")
            
            # Step 2: Handle wallet creation for recipient
            recipient_private_key = None
            
            if recipient_status == "new_user":
                # Create wallet for brand new user
                logger.info(f"[PAYMENT EXEC] Creating wallet for new user @{recipient_username}")
                
                wallet_result = self.wallet_manager.create_new_wallet()
                if not wallet_result.get("success"):
                    return {"success": False, "error": "Failed to create wallet for recipient"}
                
                recipient_wallet = wallet_result["address"]
                recipient_private_key = wallet_result["private_key"]
                
                # Encrypt with username hash
                username_hash = int(hashlib.sha256(recipient_username.encode()).hexdigest(), 16) % (10 ** 8)
                encrypted_key = self.db.encrypt_private_key(recipient_private_key, username_hash)
                
                logger.info(f"[PAYMENT EXEC] Created wallet: {recipient_wallet}")
                
            elif recipient_status == "user_no_wallet":
                # Create wallet for existing user without wallet
                logger.info(f"[PAYMENT EXEC] Creating wallet for existing user {recipient_telegram_id}")
                
                wallet_result = self.wallet_manager.create_new_wallet()
                if not wallet_result.get("success"):
                    return {"success": False, "error": "Failed to create wallet for recipient"}
                
                recipient_wallet = wallet_result["address"]
                recipient_private_key = wallet_result["private_key"]
                
                # Encrypt with telegram_id
                encrypted_key = self.db.encrypt_private_key(recipient_private_key, recipient_telegram_id)
                
                # Activate wallet in database
                self.db.activate_pending_wallet(recipient_telegram_id, recipient_wallet, encrypted_key)
                
                logger.info(f"[PAYMENT EXEC] Activated wallet for user {recipient_telegram_id}")
            
            # Step 3: Execute blockchain transfer
            logger.info(f"[PAYMENT EXEC] Executing {token} transfer to {recipient_wallet}")
            
            if token == "SOL":
                transfer_result = alchemy_transfer.execute_sol_transfer(
                    private_key=sender_private_key,
                    recipient_address=recipient_wallet,
                    amount_sol=float(amount)
                )
            else:
                transfer_result = alchemy_transfer.execute_spl_transfer(
                    private_key=sender_private_key,
                    recipient_address=recipient_wallet,
                    token_mint=token_mint,
                    amount=float(amount),
                    decimals=token_decimals
                )
            
            if not transfer_result["success"]:
                return {"success": False, "error": transfer_result.get("error", "Transaction failed")}
            
            signature = transfer_result["signature"]
            logger.info(f"[PAYMENT EXEC] Transaction submitted: {signature}")
            
            # Step 4: Poll for confirmation (30 seconds max)
            logger.info(f"[PAYMENT EXEC] Polling for confirmation...")
            confirmed = False
            
            for attempt in range(30):
                import asyncio
                await asyncio.sleep(1)
                
                status = alchemy_transfer.get_transaction_status(signature)
                logger.info(f"[PAYMENT EXEC] Attempt {attempt + 1}/30: Status = {status}")
                
                if status == "confirmed":
                    confirmed = True
                    logger.info(f"[PAYMENT EXEC] Transaction confirmed!")
                    break
                elif status == "failed":
                    return {"success": False, "error": "Transaction failed on blockchain"}
            
            if not confirmed:
                return {"success": False, "error": "Transaction confirmation timeout (check Solscan for status)"}
            
            # Step 5: Save transaction records
            tx_record = {
                "signature": signature,
                "sender_username": sender_username,
                "sender_wallet": sender_wallet,
                "recipient_username": recipient_username,
                "recipient_wallet": recipient_wallet,
                "amount": amount,
                "token": token,
                "network_fee": payment_data["network_fee"],
                "status": "confirmed"
            }
            
            # Save for sender
            self.db.save_payment_transaction(sender_telegram_id, tx_record, is_sender=True)
            
            # Save for recipient (if they have telegram_id)
            if recipient_telegram_id:
                self.db.save_payment_transaction(recipient_telegram_id, tx_record, is_sender=False)
            
            # Step 6: Create notification for recipient
            notification_data = {
                "type": "payment_received",
                "amount": amount,
                "token": token,
                "sender_username": sender_username,
                "sender_wallet": sender_wallet,
                "signature": signature,
                "timestamp": datetime.now()
            }
            
            if recipient_status == "new_user":
                # Store notification for when they start bot
                encrypted_key = self.db.encrypt_private_key(
                    recipient_private_key,
                    int(hashlib.sha256(recipient_username.encode()).hexdigest(), 16) % (10 ** 8)
                )
                
                self.db.create_pending_user_by_username(
                    username=recipient_username,
                    wallet_address=recipient_wallet,
                    encrypted_private_key=encrypted_key,
                    notification_data=notification_data
                )
                
            elif recipient_status == "pending_wallet":
                # Add to existing pending user
                self.db.add_pending_notification_by_username(recipient_username, notification_data)
                
            elif recipient_telegram_id:
                # Add notification for active user or user_no_wallet
                self.db.add_pending_notification(recipient_telegram_id, notification_data)
            
            logger.info(f"[PAYMENT EXEC] Payment completed successfully!")
            
            # Step 7: Return success
            return {
                "success": True,
                "signature": signature,
                "recipient_wallet": recipient_wallet,
                "recipient_status": recipient_status,
                "recipient_telegram_id": recipient_telegram_id,
                "sender_username": sender_username,
                "sender_wallet": sender_wallet,
                "recipient_username": recipient_username,
                "amount": amount,
                "token": token,
                "recipient_private_key": recipient_private_key  # For notification if needed
            }
            
        except Exception as e:
            logger.error(f"[PAYMENT EXEC] Error in execute_payment_secure: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ADD helper function for recipient notifications:

    async def _send_recipient_notification(self, result: Dict, context):
        """Send payment notification to recipient"""
        try:
            from telegram import Bot
            from config import TELEGRAM_BOT_TOKEN
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            
            recipient_status = result["recipient_status"]
            recipient_telegram_id = result.get("recipient_telegram_id")
            
            if not recipient_telegram_id:
                # New user or pending wallet - notification stored in DB
                logger.info(f"[NOTIFICATION] Stored notification for @{result['recipient_username']}")
                return
            
            # Format notification message
            if recipient_status == "user_no_wallet":
                recipient_private_key = result.get("recipient_private_key")
                
                message = (
                    f"<b>You Received a Payment</b>\n\n"
                    f"A wallet was created for you:\n\n"
                    f"<b>Wallet Address</b>\n"
                    f"<code>{result['recipient_wallet']}</code>\n\n"
                    f"<b>Private Key</b>\n"
                    f"<code>{recipient_private_key}</code>\n\n"
                    f"Save your private key securely and never share it with anyone.\n\n"
                    f"<b>Payment Details</b>\n"
                    f"From: @{result['sender_username']}\n"
                    f"Amount: {result['amount']} {result['token']}\n\n"
                    f"<b>Transaction</b>\n"
                    f"<code>{result['signature']}</code>\n\n"
                    f"https://solscan.io/tx/{result['signature']}"
                )
            else:
                message = (
                    f"<b>Payment Received</b>\n\n"
                    f"<b>From:</b> @{result['sender_username']}\n"
                    f"<b>Amount:</b> {result['amount']} {result['token']}\n\n"
                    f"<b>Transaction</b>\n"
                    f"<code>{result['signature']}</code>\n\n"
                    f"https://solscan.io/tx/{result['signature']}"
                )
            
            await bot.send_message(
                chat_id=recipient_telegram_id,
                text=message,
                parse_mode="HTML"
            )
            
            logger.info(f"[NOTIFICATION] Sent notification to user {recipient_telegram_id}")
            
        except Exception as e:
            logger.error(f"[NOTIFICATION] Error sending notification: {e}")
            # Don't fail the payment if notification fails
    
    # ==================== HELPER METHODS ====================
    
    def _format_telegram_response(self, text: str) -> str:
        """Clean up AI response for Telegram HTML - fix spacing and formatting issues"""
        
        # Only collapse label:value pairs, not date headers
        text = re.sub(r'<b>([^<]+)</b>:\s*\n+\s*([^<\n]+)', r'<b>\1:</b> \2', text)
        
        text = re.sub(r'</b>\s*\n+\s*<b>', r'</b>\n<b>', text)
        
        text = re.sub(r'\*\*([^\*]+)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'(?<!\*)\*([^\*]+)\*(?!\*)', r'\1', text)
        text = re.sub(r'(?<!<code>)`([^`]+)`(?!</code>)', r'<code>\1</code>', text)
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        
        solscan_pattern = r'(?<!href=["\'])(?<!">)(https://solscan\.io/tx/[a-zA-Z0-9]+)(?!</a>)'
        text = re.sub(
            solscan_pattern,
            lambda m: f'<a href="{m.group(1)}">View on Solscan</a>',
            text
        )
        
        def wrap_address_if_needed(match):
            addr = match.group(1)
            start = match.start()
            before_text = text[max(0, start-15):start]
            if '<code>' in before_text or 'tx/' in before_text or 'href=' in before_text:
                return addr
            return f'<code>{addr}</code>'
        
        address_pattern = r'(?<!<code>)(?<!tx/)(?<!href=["\'])\b([1-9A-HJ-NP-Za-km-z]{32,44})\b(?!</code>)(?!</a>)'
        text = re.sub(address_pattern, wrap_address_if_needed, text)
        
        text = re.sub(r'^[-•]\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
        
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        lines = text.split('\n')
        cleaned_lines = []
        prev_was_data = False
        
        for line in lines:
            is_data_line = bool(re.match(r'^<b>[^<]+</b>:?\s*.+', line.strip()))
            
            if is_data_line and prev_was_data:
                if cleaned_lines and cleaned_lines[-1] == '':
                    cleaned_lines.pop()
            
            cleaned_lines.append(line)
            prev_was_data = is_data_line
        
        text = '\n'.join(cleaned_lines)
        
        text = re.sub(r'(\n\s*){3,}', '\n\n', text)
        
        return text.strip()

    
    async def _execute_signal_swap(self, query, signal_id, signal_data, amount):
        """Execute swap from signal"""
        try:
            await query.edit_message_text(f"Swapping {amount} SOL...")
            
            # Get user data
            user_id = signal_data["user_id"]
            user = self.db.get_user(user_id)
            wallet_address = user["wallet_address"]
            private_key = self.db.get_decrypted_private_key(user_id)
            
            # Execute swap
            result = await self.jupiter.execute_swap(
                wallet_address=wallet_address,
                private_key=private_key,
                input_token="SOL",
                output_token=signal_data["token_address"],
                amount=str(amount),
                slippage_percent=user.get("slippage_percent", 5)
            )
            
            if result.get("success"):
                # Update statistics
                self.db.add_transaction(user_id, {
                    "tx_id": signal_id,
                    "signature": result["signature"],
                    "type": "swap",
                    "source": "signal",
                    "input_token": "SOL",
                    "output_token": signal_data["token_address"],
                    "input_amount": amount,
                    "status": "success"
                })
                
                await query.edit_message_text(
                    f"<b>Swap Successful</b>\n\n"
                    f"Swapped {amount} SOL for {signal_data['token_data']['symbol']}\n\n"
                    f"<b>Transaction</b>\n"
                    f"<code>{result['signature']}</code>\n\n"
                    f"https://solscan.io/tx/{result['signature']}",
                    parse_mode="HTML"
                )
            else:
                await query.edit_message_text(f"Swap failed: {result.get('error')}")
            
            # Clean up
            del pending_signal_swaps[signal_id]
        
        except Exception as e:
            logger.error(f"Signal swap execution error: {e}")
            await query.edit_message_text("Swap failed")