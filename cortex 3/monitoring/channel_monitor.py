"""
Cortex Unified Bot - Channel Monitor
Production-ready version for monitoring Telegram channels for trading signals

IMPORTANT NOTE ON TELEGRAM CHANNELS:
In Telegram CHANNELS (not groups), ONLY admins/owners can post messages.
Regular members can only view content. Therefore, we do NOT need admin checks -
any message in a channel is by definition from an admin.
"""

import asyncio
import json
import re
import uuid
import logging
from typing import Dict, Optional, Callable, List
from datetime import datetime

from telethon import TelegramClient, events
from telethon.tl.types import Channel
from telethon.tl.functions.channels import JoinChannelRequest
from openai import OpenAI

from config import (
    TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE,
    OPENAI_API_KEY, OPENAI_MODEL
)
from services.jupiter_swap import JupiterAPI
from services.twilio_calls import TwilioHandler

logger = logging.getLogger(__name__)

# Global instance
channel_monitor_instance = None


class ChannelMonitor:
    """
    Monitors Telegram channels for trading signals
    
    Architecture:
    - Uses Telethon for real-time message monitoring
    - Uses OpenAI for signal classification
    - Uses Jupiter API for token data
    - Uses Celery for async call/swap processing
    """
    
    def __init__(self, database):
        """Initialize monitor"""
        global channel_monitor_instance
        
        self.db = database
        self.loop = None
        self._running = True
        
        # Initialize Telethon client
        self.client = TelegramClient(
            'cortex_monitor_session',
            TELEGRAM_API_ID,
            TELEGRAM_API_HASH
        )
        
        # Initialize services
        self.openai = OpenAI(api_key=OPENAI_API_KEY)
        self.jupiter = JupiterAPI()
        self.twilio = TwilioHandler(self.db)
        
        # Active channels: {channel_id: [user_ids]}
        self.active_channels = {}
        
        # Signal callback for bot handlers
        self.signal_callback: Optional[Callable] = None
        
        # Set global instance
        channel_monitor_instance = self
        
        logger.info("[MONITOR] Channel monitor initialized")
    
    def set_signal_callback(self, callback: Callable):
        """Set callback for when signals are detected"""
        self.signal_callback = callback
    
    # ==================== MAIN LOOP ====================
    
    async def run(self):
        """Start monitoring channels - non-blocking main loop"""
        try:
            self.loop = asyncio.get_event_loop()
            
            # Start Telethon client
            await self.client.start(phone=TELEGRAM_PHONE)
            logger.info("[MONITOR] Telethon client started")
            
            # Load active channels from database
            await self.load_active_channels()
            
            logger.info(f"[MONITOR] Loaded {len(self.active_channels)} channels from database")
            logger.info(f"[MONITOR] Channel IDs: {list(self.active_channels.keys())}")
            
            # Register message handler if we have channels
            if self.active_channels:
                self.client.add_event_handler(
                    self.handle_channel_message,
                    events.NewMessage(chats=list(self.active_channels.keys()))
                )
                logger.info(f"[MONITOR] Message handler registered for {len(self.active_channels)} channels")
            
            # Start validation task (hourly check)
            asyncio.create_task(self.validate_active_channels())
            
            # Main loop - yields control to allow other tasks
            logger.info("[MONITOR] Starting main loop (non-blocking)")
            while self._running:
                try:
                    await asyncio.sleep(1)
                    
                    # Reconnect if disconnected
                    if not self.client.is_connected():
                        logger.warning("[MONITOR] Client disconnected, reconnecting...")
                        await self.client.connect()
                    
                except asyncio.CancelledError:
                    logger.info("[MONITOR] Main loop cancelled")
                    break
                except Exception as e:
                    logger.error(f"[MONITOR] Loop error: {e}")
                    await asyncio.sleep(5)
            
            logger.info("[MONITOR] Main loop ended")
            
        except Exception as e:
            logger.error(f"[MONITOR] Fatal error: {e}", exc_info=True)
            raise
        finally:
            if self.client.is_connected():
                await self.client.disconnect()
    
    async def stop(self):
        """Stop the monitor gracefully"""
        logger.info("[MONITOR] Stopping...")
        self._running = False
        if self.client.is_connected():
            await self.client.disconnect()
    
    # ==================== CHANNEL MANAGEMENT ====================
    
    async def load_active_channels(self):
        """Load all monitored channels from database"""
        try:
            self.active_channels = self.db.get_all_monitored_channels()
            logger.info(f"[MONITOR] Loaded {len(self.active_channels)} channels")
        except Exception as e:
            logger.error(f"[MONITOR] Error loading channels: {e}")
            self.active_channels = {}
    
    async def validate_active_channels(self):
        """Periodically validate that monitored channels still exist"""
        while self._running:
            try:
                await asyncio.sleep(3600)  # Check every hour
                
                invalid_channels = []
                for channel_id in list(self.active_channels.keys()):
                    try:
                        channel = await self.client.get_entity(channel_id)
                        if not channel:
                            invalid_channels.append(channel_id)
                    except Exception:
                        invalid_channels.append(channel_id)
                
                for channel_id in invalid_channels:
                    logger.warning(f"[MONITOR] Channel {channel_id} no longer valid, removing...")
                    del self.active_channels[channel_id]
                
                if invalid_channels:
                    self._update_event_handler()
                    
            except Exception as e:
                logger.error(f"[MONITOR] Validation error: {e}")
    
    async def add_channel_monitoring(self, user_id: int, channel_username: str) -> dict:
        """
        Add channel to monitoring
        
        Args:
            user_id: Telegram user ID requesting monitoring
            channel_username: Channel username (with or without @)
            
        Returns:
            Dictionary with success status and channel info
        """
        try:
            channel_username = channel_username.lstrip('@')
            logger.info(f"[ADD_CHANNEL] Adding @{channel_username} for user {user_id}")
            
            # Join channel and get entity
            channel_entity = await self.join_channel(channel_username)
            
            if not channel_entity:
                return {
                    "success": False, 
                    "error": f"Could not join @{channel_username}. Make sure it's a public channel."
                }
            
            # Extract channel information
            raw_channel_id = channel_entity.id
            
            # Convert to proper Telegram format (channels need -100 prefix)
            if raw_channel_id > 0:
                channel_id = int(f"-100{raw_channel_id}")
            else:
                channel_id = raw_channel_id
            
            channel_title = getattr(channel_entity, 'title', channel_username) or "Unknown Channel"
            participants_count = getattr(channel_entity, 'participants_count', None)
            
            logger.info(f"[ADD_CHANNEL] Joined @{channel_username} (ID: {channel_id})")
            logger.info(f"[ADD_CHANNEL] Title: {channel_title}, Members: {participants_count}")
            
            # Add to active channels
            if channel_id not in self.active_channels:
                self.active_channels[channel_id] = []
            
            if user_id not in self.active_channels[channel_id]:
                self.active_channels[channel_id].append(user_id)
            
            # Update event handler
            self._update_event_handler()
            
            logger.info(f"[ADD_CHANNEL] User {user_id} now monitoring @{channel_username}")
            logger.info(f"[ADD_CHANNEL] Total active channels: {len(self.active_channels)}")
            
            return {
                "success": True,
                "channel_id": channel_id,
                "channel_username": channel_username,
                "channel_title": channel_title,
                "members_count": participants_count,
                "is_private": participants_count is None,
                "channel_link": f"https://t.me/{channel_username}"
            }
            
        except Exception as e:
            logger.error(f"[ADD_CHANNEL] Error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def remove_channel_monitoring(self, user_id: int, channel_username: str) -> bool:
        """Remove channel from user's monitoring list"""
        try:
            channel_username = channel_username.lstrip('@')
            
            # Find channel by username in active channels
            for channel_id, users in list(self.active_channels.items()):
                if user_id in users:
                    users.remove(user_id)
                    
                    # If no users left, remove channel entirely
                    if not users:
                        del self.active_channels[channel_id]
                    
                    self._update_event_handler()
                    
                    logger.info(f"[REMOVE_CHANNEL] User {user_id} stopped monitoring channel {channel_id}")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"[REMOVE_CHANNEL] Error: {e}")
            return False
    
    async def join_channel(self, channel_username: str):
        """Join a Telegram channel and return channel entity"""
        try:
            channel_username = channel_username.lstrip('@')
            logger.info(f"[JOIN] Attempting to join @{channel_username}")
            
            # Get channel entity
            try:
                channel = await self.client.get_entity(channel_username)
            except Exception as e:
                if "cannot find" in str(e).lower():
                    logger.error(f"[JOIN] Channel @{channel_username} not found")
                    return None
                raise
            
            if not isinstance(channel, Channel):
                logger.error(f"[JOIN] @{channel_username} is not a channel")
                return None
            
            # Check if private/restricted
            if channel.broadcast and channel.restricted:
                logger.warning(f"[JOIN] Channel @{channel_username} is private/restricted")
                return None
            
            # Try to join
            try:
                await self.client(JoinChannelRequest(channel))
                logger.info(f"[JOIN] Successfully joined @{channel_username}")
            except Exception as join_error:
                error_msg = str(join_error).lower()
                if "flood" in error_msg:
                    logger.error(f"[JOIN] Rate limited for @{channel_username}")
                    return None
                elif "banned" in error_msg:
                    logger.error(f"[JOIN] Banned from @{channel_username}")
                    return None
                elif "already" in error_msg or "participant" in error_msg:
                    logger.info(f"[JOIN] Already member of @{channel_username}")
                else:
                    logger.warning(f"[JOIN] Join warning: {join_error}")
            
            return channel
            
        except Exception as e:
            logger.error(f"[JOIN] Error: {e}", exc_info=True)
            return None
    
    def _update_event_handler(self):
        """Update the message event handler with current channel list"""
        try:
            # Remove existing handler
            self.client.remove_event_handler(self.handle_channel_message)
            
            # Add new handler if we have channels
            if self.active_channels:
                self.client.add_event_handler(
                    self.handle_channel_message,
                    events.NewMessage(chats=list(self.active_channels.keys()))
                )
                logger.info(f"[MONITOR] Event handler updated for {len(self.active_channels)} channels")
        except Exception as e:
            logger.error(f"[MONITOR] Error updating event handler: {e}")
    
    # ==================== MESSAGE HANDLING ====================
    
    async def handle_channel_message(self, event):
        """
        Handle new message from monitored channel
        
        NOTE: In Telegram channels, ONLY admins can post messages.
        Regular members can only view. No admin check is needed.
        """
        try:
            channel_id = event.chat_id
            
            # Verify we're monitoring this channel
            if channel_id not in self.active_channels:
                return
            
            # Get channel info
            channel = await event.get_chat()
            channel_username = getattr(channel, 'username', None) or str(channel_id)
            
            # Get message text
            text = event.message.text if event.message else ""
            
            if not text:
                return  # Ignore non-text messages
            
            logger.info(f"[SIGNAL] ===== NEW MESSAGE =====")
            logger.info(f"[SIGNAL] Channel: @{channel_username}")
            logger.info(f"[SIGNAL] Preview: {text[:100]}...")
            
            # Extract contract address
            contract_address = self.extract_contract_address(text)
            
            if not contract_address:
                logger.debug(f"[SIGNAL] No contract address found - skipping")
                return
            
            logger.info(f"[SIGNAL] ✅ Contract address: {contract_address}")
            
            # Classify message with AI
            classification = await self.classify_message(text)
            
            if not classification:
                logger.warning(f"[SIGNAL] Classification failed")
                return
            
            signal_type = classification.get("classification", "OTHER")
            confidence = classification.get("confidence", 0)
            
            if signal_type != "BUY":
                logger.info(f"[SIGNAL] Classified as {signal_type} ({confidence:.0%}) - skipping")
                return
            
            logger.info(f"[SIGNAL] 🚀 BUY SIGNAL ({confidence:.0%} confidence)")
            
            # Get token data from Jupiter
            token_data = await self.jupiter.get_token_info(contract_address)
            
            if not token_data:
                logger.warning(f"[SIGNAL] Could not get token data for {contract_address}")
                return
            
            token_name = token_data.get('name', 'Unknown')
            token_symbol = token_data.get('symbol', 'UNKNOWN')
            
            logger.info(f"[SIGNAL] Token: {token_name} ({token_symbol})")
            
            # Process for all users monitoring this channel
            users = self.active_channels.get(channel_id, [])
            logger.info(f"[SIGNAL] Notifying {len(users)} user(s)")
            
            for user_id in users:
                await self.process_signal_for_user(
                    user_id=user_id,
                    channel_username=channel_username,
                    contract_address=contract_address,
                    token_data=token_data,
                    classification=classification
                )
            
            logger.info(f"[SIGNAL] ===== PROCESSED =====")
            
        except Exception as e:
            logger.error(f"[SIGNAL] Error: {e}", exc_info=True)
    
    def extract_contract_address(self, text: str) -> Optional[str]:
        """Extract Solana contract address from text"""
        # Solana addresses are 32-44 chars, base58 encoded
        pattern = r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b'
        matches = re.findall(pattern, text)
        
        return matches[0] if matches else None
    
    async def classify_message(self, text: str) -> Optional[Dict]:
        """Classify message as BUY/SELL/OTHER using AI"""
        try:
            system_prompt = """You are a trading signal classifier for cryptocurrency.

Classify messages as:
- BUY: Contains buy indicators like "buy", "entry", "gem", "moon", "pump", "bullish", "ape in", "send it"
- SELL: Contains sell indicators like "sell", "exit", "dump", "take profit", "bearish", "get out"
- OTHER: No clear trading signal

Be aggressive on BUY detection - false positives are better than missing opportunities.

Respond with ONLY valid JSON:
{"classification": "BUY", "confidence": 0.85, "reasoning": "Contains buy keyword"}"""
            
            response = self.openai.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Classify:\n\n{text[:500]}"}
                ],
                temperature=0.3,
                max_tokens=100
            )
            
            content = response.choices[0].message.content.strip()
            
            # Parse JSON response
            return json.loads(content)
            
        except json.JSONDecodeError as e:
            logger.error(f"[CLASSIFY] JSON parse error: {e}")
            return None
        except Exception as e:
            logger.error(f"[CLASSIFY] Error: {e}")
            return None
    
    async def process_signal_for_user(self, user_id: int, channel_username: str,
                                      contract_address: str, token_data: Dict,
                                      classification: Dict):
        """Process detected signal for a specific user"""
        try:
            user = self.db.get_user(user_id)
            if not user:
                logger.warning(f"[PROCESS] User {user_id} not found in database")
                return
            
            # Check if user has calls enabled for this channel
            user_channels = user.get("active_channels", [])
            channel_config = next(
                (ch for ch in user_channels if ch.get("channel_username") == channel_username),
                None
            )
            
            calls_enabled = True
            if channel_config:
                calls_enabled = channel_config.get("calls_enabled", True)
            
            # Queue call task via Celery
            if calls_enabled and user.get("calls_enabled", True):
                from tasks import make_call_task
                
                make_call_task.apply_async(
                    args=[user_id, token_data, channel_username],
                    queue='urgent',
                    priority=10
                )
                logger.info(f"[PROCESS] Call queued for user {user_id}")
            
            # Save to signal history
            signal_data = {
                "signal_id": str(uuid.uuid4())[:8],
                "channel_username": channel_username,
                "token_address": contract_address,
                "token_name": token_data.get("name"),
                "token_symbol": token_data.get("symbol"),
                "classification": classification.get("classification"),
                "confidence": classification.get("confidence"),
                "call_made": calls_enabled,
                "detected_at": datetime.now()
            }
            
            self.db.add_signal_to_history(user_id, signal_data)
            
            logger.info(f"[PROCESS] Signal saved for user {user_id}: {token_data.get('symbol')}")
            
        except Exception as e:
            logger.error(f"[PROCESS] Error: {e}", exc_info=True)
    
    # ==================== UTILITY METHODS ====================
    
    async def get_channel_id(self, channel_username: str) -> Optional[int]:
        """Get channel ID by username"""
        try:
            channel = await self.client.get_entity(channel_username.lstrip('@'))
            if isinstance(channel, Channel):
                raw_id = channel.id
                return int(f"-100{raw_id}") if raw_id > 0 else raw_id
            return None
        except Exception:
            return None