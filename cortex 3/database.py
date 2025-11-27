"""
Cortex Unified Bot - Database Operations
Handles all MongoDB operations for users, wallets, channels, and transactions
"""

import logging
import hashlib
import base64
from datetime import datetime
from typing import Optional, Dict, List, Any
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError, DuplicateKeyError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, mongodb_uri: str, database_name: str, collection_name: str, encryption_key: str):
        """Initialize database connection and encryption"""
        try:
            self.client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
            self.db = self.client[database_name]
            self.users = self.db[collection_name]
            self.encryption_key = encryption_key.encode()
            
            # Test connection
            self.client.server_info()
            logger.info("MongoDB connected successfully")
            
            # Create indexes
            self._create_indexes()
            
        except PyMongoError as e:
            logger.error(f"MongoDB connection failed: {e}")
            raise
    
    def _create_indexes(self):
        """Create database indexes for performance"""
        try:
            self.users.create_index("telegram_id", unique=True)
            self.users.create_index("username", unique=True, sparse=True)
            self.users.create_index("wallet_address", sparse=True)
            self.users.create_index("pending_wallet_username", sparse=True)
            self.users.create_index("phone_number", sparse=True)
            self.users.create_index("active_channels.channel_id")
            self.users.create_index("last_active")
            logger.info("Database indexes created")
        except PyMongoError as e:
            logger.warning(f"Index creation warning: {e}")
    
    # ==================== ENCRYPTION UTILITIES ====================
    
    def derive_key_from_telegram_id(self, telegram_id: int) -> Fernet:
        """Derive unique encryption key per user"""
        salt = hashlib.sha256(str(telegram_id).encode()).digest()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.encryption_key))
        return Fernet(key)
    
    def encrypt_private_key(self, private_key: str, telegram_id: int) -> str:
        """Encrypt private key with user-specific cipher"""
        user_cipher = self.derive_key_from_telegram_id(telegram_id)
        return user_cipher.encrypt(private_key.encode()).decode()
    
    def decrypt_private_key(self, encrypted_key: str, telegram_id: int) -> str:
        """Decrypt private key with user-specific cipher"""
        user_cipher = self.derive_key_from_telegram_id(telegram_id)
        return user_cipher.decrypt(encrypted_key.encode()).decode()
    
    # ==================== USER OPERATIONS ====================
    
    def get_user(self, telegram_id: int) -> Optional[Dict]:
        """Get user from database"""
        try:
            return self.users.find_one({"telegram_id": telegram_id})
        except PyMongoError as e:
            logger.error(f"Database error getting user: {e}")
            return None
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get user by username (normalized)"""
        try:
            normalized = username.lstrip('@').lower()
            return self.users.find_one({"username": normalized})
        except PyMongoError as e:
            logger.error(f"Database error getting user by username: {e}")
            return None
    
    def create_user(self, telegram_id: int, username: str) -> Optional[Dict]:
        """Create new user"""
        try:
            normalized_username = username.lstrip('@').lower() if username else None
            
            user_data = {
                "telegram_id": telegram_id,
                "username": normalized_username,
                
                # Wallet Information
                "wallet_address": None,
                "encrypted_private_key": None,
                "wallet_type": None,
                "wallet_created_at": None,
                
                # Phone & Call Settings
                "phone_number": None,
                "phone_verified": False,
                "calls_enabled": True,
                
                # Trading Settings
                "slippage_percent": 5,
                "max_trade_amount_sol": 5.0,
                
                # Channel Monitoring
                "active_channels": [],
                
                # Signal & Transaction History
                "signal_history": [],
                "transactions": [],
                
                # Payment System
                "pending_notifications": [],
                "pending_wallet_address": None,
                "pending_private_key": None,
                
                # AI Context
                "previous_response_id": None,
                
                # Timestamps
                "created_at": datetime.now(),
                "last_active": datetime.now(),
                "last_updated": datetime.now(),
                
                # Statistics
                "total_calls": 0,
                "total_calls_responded": 0,
                "total_swaps_signal": 0,
                "total_swaps_user": 0,
                "total_volume_sol": 0.0,
                "total_payments_sent": 0,
                "total_payments_received": 0
            }
            
            self.users.insert_one(user_data)
            logger.info(f"Created user: {normalized_username} ({telegram_id})")
            return user_data
            
        except DuplicateKeyError:
            logger.warning(f"User already exists: {telegram_id}")
            return self.get_user(telegram_id)
        except PyMongoError as e:
            logger.error(f"Database error creating user: {e}")
            return None
    
    def update_user_activity(self, telegram_id: int, previous_response_id: str = None):
        """Update user activity timestamp and AI context"""
        try:
            update_data = {"last_active": datetime.now()}
            if previous_response_id is not None:
                update_data["previous_response_id"] = previous_response_id
            
            self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": update_data}
            )
        except PyMongoError as e:
            logger.error(f"Database error updating activity: {e}")
    
    def user_has_wallet(self, telegram_id: int) -> bool:
        """Check if user has wallet"""
        user = self.get_user(telegram_id)
        return user and user.get("wallet_address") is not None
    
    # ==================== WALLET OPERATIONS ====================
    
    def save_wallet(self, telegram_id: int, wallet_address: str, private_key: str, wallet_type: str) -> bool:
        """Save wallet to database"""
        try:
            encrypted_key = self.encrypt_private_key(private_key, telegram_id)
            
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": {
                    "wallet_address": wallet_address,
                    "encrypted_private_key": encrypted_key,
                    "wallet_type": wallet_type,
                    "wallet_created_at": datetime.now(),
                    "last_updated": datetime.now()
                }}
            )
            
            if result.matched_count == 0:
                logger.warning(f"No user found with telegram_id {telegram_id} - cannot save wallet")
                return False
            
            if result.modified_count > 0:
                logger.info(f"Wallet saved for user {telegram_id}")
                return True
            else:
                # Document found but not modified (wallet data might be identical)
                logger.info(f"Wallet data unchanged for user {telegram_id}")
                return True
            
        except Exception as e:
            logger.error(f"Error saving wallet: {e}")
            return False
    
    def get_decrypted_private_key(self, telegram_id: int) -> Optional[str]:
        """Get and decrypt user's private key"""
        user = self.get_user(telegram_id)
        if not user or not user.get("encrypted_private_key"):
            return None
        
        return self.decrypt_private_key(user["encrypted_private_key"], telegram_id)
    
    # ==================== PENDING WALLET OPERATIONS ====================
    
    def create_pending_wallet(self, username: str, wallet_address: str, private_key: str, notification: Dict) -> bool:
        """Create pending wallet for unregistered user"""
        try:
            normalized_username = username.lstrip('@').lower()
            
            # Check if user exists
            existing = self.get_user_by_username(normalized_username)
            if existing:
                # User exists, add notification
                return self.add_pending_notification(existing["telegram_id"], notification)
            
            # Create pending user entry
            user_data = {
                "telegram_id": None,  # Will be set on claim
                "username": normalized_username,
                "pending_wallet_username": normalized_username,
                "pending_wallet_address": wallet_address,
                "pending_private_key": private_key,  # Not encrypted yet
                "pending_notifications": [notification],
                "created_at": datetime.now(),
                "is_pending": True
            }
            
            self.users.insert_one(user_data)
            logger.info(f"Created pending wallet for {normalized_username}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating pending wallet: {e}")
            return False
        
    def toggle_channel_calls(self, telegram_id: int, channel_username: str, enabled: bool) -> bool:
        """Enable/disable calls for specific channel"""
        try:
            channel_username = channel_username.lstrip('@').lower()
            
            user = self.users.find_one({"telegram_id": telegram_id})
            if not user:
                return False
            
            channels = user.get("active_channels", [])
            channel_found = False
            
            for channel in channels:
                if channel.get("channel_username", "").lower() == channel_username:
                    channel_found = True
                    break
            
            if not channel_found:
                logger.warning(f"Channel {channel_username} not found for user {telegram_id}")
                return False
            
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {
                    "$set": {
                        "active_channels.$[elem].calls_enabled": enabled,
                        "last_updated": datetime.now()
                    }
                },
                array_filters=[{"elem.channel_username": {"$regex": f"^{channel_username}$", "$options": "i"}}]
            )
            
            return result.matched_count > 0
            
        except PyMongoError as e:
            logger.error(f"Toggle channel calls error: {e}")
            return False
    
    def claim_pending_wallet(self, telegram_id: int, username: str) -> Dict:
        """Claim pending wallet when user joins"""
        try:
            normalized_username = username.lstrip('@').lower()
            
            # Find pending wallet by username (no telegram_id set)
            pending = self.users.find_one({
                "username": normalized_username,
                "telegram_id": None
            })
            
            if not pending or not pending.get("pending_wallet_address"):
                return {"success": False}
            
            # Decrypt private key using username hash
            username_hash = int(hashlib.sha256(normalized_username.encode()).hexdigest(), 16) % (10 ** 8)
            private_key = self.decrypt_private_key(pending["pending_private_key"], username_hash)
            
            # Re-encrypt with telegram_id
            encrypted_key = self.encrypt_private_key(private_key, telegram_id)
            
            # Update user record with telegram_id and activate wallet
            self.users.update_one(
                {"username": normalized_username, "telegram_id": None},
                {
                    "$set": {
                        "telegram_id": telegram_id,
                        "wallet_address": pending["pending_wallet_address"],
                        "encrypted_private_key": encrypted_key,
                        "wallet_type": "auto_created",
                        "wallet_created_at": datetime.now(),
                        "last_updated": datetime.now(),
                        # Clear pending fields
                        "pending_wallet_address": None,
                        "pending_private_key": None
                    }
                }
            )
            
            logger.info(f"Claimed pending wallet for {normalized_username} -> {telegram_id}")
            
            return {
                "success": True,
                "wallet_address": pending["pending_wallet_address"],
                "private_key": private_key,
                "notifications": pending.get("pending_notifications", [])
            }
            
        except Exception as e:
            logger.error(f"Error claiming pending wallet: {e}")
            return {"success": False, "error": str(e)}
    
    def add_pending_notification(self, telegram_id: int, notification: Dict) -> bool:
        """Add pending notification for user"""
        try:
            self.users.update_one(
                {"telegram_id": telegram_id},
                {"$push": {"pending_notifications": notification}}
            )
            return True
        except Exception as e:
            logger.error(f"Error adding notification: {e}")
            return False
    

    def create_pending_user_by_username(self, username: str, wallet_address: str, 
                                        encrypted_private_key: str, notification_data: Dict) -> bool:
        """Create pending user entry for users who haven't started bot yet"""
        try:
            normalized_username = username.lstrip('@').lower()
            
            # Check if user already exists
            existing = self.users.find_one({"username": normalized_username})
            if existing:
                # User exists, just add notification
                if existing.get("telegram_id"):
                    # Active user, add to pending_notifications
                    return self.add_pending_notification(existing["telegram_id"], notification_data)
                else:
                    # Pending user, add another notification
                    return self.add_pending_notification_by_username(normalized_username, notification_data)
            
            # Create new pending user
            user_data = {
                "telegram_id": None,  # Will be set when they start bot
                "username": normalized_username,
                
                # Wallet Information (pending)
                "wallet_address": None,  # Not active yet
                "encrypted_private_key": None,  # Not active yet
                "pending_wallet_address": wallet_address,
                "pending_private_key": encrypted_private_key,  # Encrypted!
                "wallet_type": "auto_created_pending",
                "wallet_created_at": None,
                
                # Notifications
                "pending_notifications": [notification_data],
                
                # Phone & Call Settings
                "phone_number": None,
                "phone_verified": False,
                "calls_enabled": True,
                
                # Trading Settings
                "slippage_percent": 5,
                "max_trade_amount_sol": 5.0,
                
                # Channel Monitoring
                "active_channels": [],
                
                # Signal & Transaction History
                "signal_history": [],
                "transactions": [],
                
                # AI Context
                "previous_response_id": None,
                
                # Timestamps
                "created_at": datetime.now(),
                "last_active": datetime.now(),
                "last_updated": datetime.now(),
                
                # Statistics
                "total_calls": 0,
                "total_calls_responded": 0,
                "total_swaps_signal": 0,
                "total_swaps_user": 0,
                "total_volume_sol": 0.0,
                "total_payments_sent": 0,
                "total_payments_received": 0
            }
            
            self.users.insert_one(user_data)
            logger.info(f"Created pending user: {normalized_username} with wallet {wallet_address}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating pending user: {e}")
            return False

    def add_pending_notification_by_username(self, username: str, notification_data: Dict) -> bool:
        """Add notification to pending user by username (no telegram_id)"""
        try:
            normalized_username = username.lstrip('@').lower()
            
            result = self.users.update_one(
                {"username": normalized_username, "telegram_id": None},
                {"$push": {"pending_notifications": notification_data}}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Error adding notification by username: {e}")
            return False

    def activate_pending_wallet(self, telegram_id: int, wallet_address: str, encrypted_private_key: str) -> bool:
        """Activate pending wallet for existing user (has telegram_id but no wallet)"""
        try:
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": {
                    "wallet_address": wallet_address,
                    "encrypted_private_key": encrypted_private_key,
                    "wallet_type": "auto_created",
                    "wallet_created_at": datetime.now(),
                    "last_updated": datetime.now(),
                    # Clear pending fields
                    "pending_wallet_address": None,
                    "pending_private_key": None
                }}
            )
            
            logger.info(f"Activated wallet for user {telegram_id}")
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Error activating pending wallet: {e}")
            return False

    def save_payment_transaction(self, telegram_id: int, tx_data: Dict, is_sender: bool = True) -> bool:
        """Save payment transaction to user's history"""
        try:
            transaction = {
                "tx_id": tx_data.get("signature"),
                "signature": tx_data.get("signature"),
                "type": "outgoing_payment" if is_sender else "incoming_payment",
                "source": "user_payment",
                "sender_username": tx_data.get("sender_username"),
                "sender_wallet": tx_data.get("sender_wallet"),
                "recipient_username": tx_data.get("recipient_username"),
                "recipient_wallet": tx_data.get("recipient_wallet"),
                "amount": tx_data.get("amount"),
                "token": tx_data.get("token"),
                "network_fee": tx_data.get("network_fee"),
                "timestamp": datetime.now(),
                "status": tx_data.get("status", "confirmed"),
                "metadata": tx_data.get("metadata", {})
            }
            
            # Update transaction history and statistics
            update_data = {
                "$push": {"transactions": transaction},
                "$set": {"last_updated": datetime.now()}
            }
            
            # Update payment statistics
            if is_sender:
                update_data["$inc"] = {"total_payments_sent": 1}
            else:
                update_data["$inc"] = {"total_payments_received": 1}
            
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                update_data
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Error saving payment transaction: {e}")
            return False

    def clear_pending_notifications(self, telegram_id: int) -> bool:
        """Clear pending notifications after delivery"""
        try:
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": {"pending_notifications": []}}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Error clearing notifications: {e}")
            return False

    
    # ==================== CHANNEL MONITORING OPERATIONS ====================
    
    def add_channel_monitoring(self, telegram_id: int, channel_username: str, channel_id: int) -> bool:
        """Add channel to monitoring - prevents duplicates"""
        try:
            # Check if already monitoring
            user = self.get_user(telegram_id)
            if user:
                channels = user.get("active_channels", [])
                for ch in channels:
                    if ch["channel_username"] == channel_username:
                        logger.info(f"User already monitoring {channel_username}")
                        return False  # Already monitoring
            
            # Add new channel
            channel_data = {
                "channel_username": channel_username,
                "channel_id": channel_id,
                "added_at": datetime.now(),
                "is_active": True,
                "calls_enabled": True,
                "total_signals": 0,
                "total_calls": 0
            }
            
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {
                    "$addToSet": {"active_channels": channel_data},  # $addToSet prevents duplicates
                    "$set": {"last_updated": datetime.now()}
                }
            )
            
            return result.modified_count > 0
            
        except PyMongoError as e:
            logger.error(f"Add channel error: {e}")
            return False
    
    def remove_channel_monitoring(self, telegram_id: int, channel_username: str) -> bool:
        """Remove channel from monitoring"""
        try:
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {
                    "$pull": {"active_channels": {"channel_username": channel_username}},
                    "$set": {"last_updated": datetime.now()}
                }
            )
            
            return result.modified_count > 0
            
        except PyMongoError as e:
            logger.error(f"Remove channel error: {e}")
            return False
    
    def get_active_channels(self, telegram_id: int) -> List[Dict]:
        """Get all active channels for a user"""
        user = self.get_user(telegram_id)
        if not user:
            return []
        
        return [ch for ch in user.get("active_channels", []) if ch.get("is_active", True)]
    
    def get_all_monitored_channels(self) -> Dict[int, List[int]]:
        """Get all channels being monitored by all users"""
        try:
            channels = {}  # {channel_id: [user_telegram_ids]}
            
            users = self.users.find({"active_channels": {"$exists": True, "$ne": []}})
            
            for user in users:
                if not user.get("calls_enabled", True):
                    continue
                    
                for channel in user.get("active_channels", []):
                    if channel.get("is_active", True) and channel.get("calls_enabled", True):
                        channel_id = channel["channel_id"]
                        if channel_id not in channels:
                            channels[channel_id] = []
                        channels[channel_id].append(user["telegram_id"])
            
            return channels
            
        except PyMongoError as e:
            logger.error(f"Get all monitored channels error: {e}")
            return {}
    
    # ==================== SIGNAL & STATISTICS OPERATIONS ====================
    
    def add_signal_to_history(self, telegram_id: int, signal_data: Dict) -> bool:
        """Add detected signal to history"""
        try:
            signal_record = {
                "signal_id": signal_data.get("signal_id"),
                "channel_username": signal_data.get("channel_username"),
                "token_address": signal_data.get("token_address"),
                "token_name": signal_data.get("token_name"),
                "token_symbol": signal_data.get("token_symbol"),
                "classification": signal_data.get("classification"),
                "confidence": signal_data.get("confidence"),
                "detected_at": datetime.now(),
                "call_made": signal_data.get("call_made", False),
                "call_responded": signal_data.get("call_responded", False),
                "call_duration_seconds": signal_data.get("call_duration_seconds"),
                "swap_executed": signal_data.get("swap_executed", False),
                "swap_amount_sol": signal_data.get("swap_amount_sol"),
                "swap_signature": signal_data.get("swap_signature")
            }
            
            # Update signal history and channel statistics
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {
                    "$push": {"signal_history": signal_record},
                    "$inc": {
                        f"active_channels.$[elem].total_signals": 1,
                        f"active_channels.$[elem].total_calls": 1 if signal_data.get("call_made") else 0
                    },
                    "$set": {"last_updated": datetime.now()}
                },
                array_filters=[{"elem.channel_username": signal_data.get("channel_username")}]
            )
            
            return result.modified_count > 0
            
        except PyMongoError as e:
            logger.error(f"Add signal error: {e}")
            return False
    
    def add_transaction(self, telegram_id: int, tx_data: Dict) -> bool:
        """Add transaction to history"""
        try:
            transaction = {
                "tx_id": tx_data.get("tx_id"),
                "signature": tx_data.get("signature"),
                "type": tx_data.get("type"),  # "swap", "transfer", "receive"
                "source": tx_data.get("source"),  # "signal", "user", "payment"
                "input_token": tx_data.get("input_token"),
                "output_token": tx_data.get("output_token"),
                "input_amount": tx_data.get("input_amount"),
                "output_amount": tx_data.get("output_amount"),
                "price_usd": tx_data.get("price_usd"),
                "timestamp": datetime.now(),
                "status": tx_data.get("status", "success"),
                "metadata": tx_data.get("metadata", {})
            }
            
            # Update transaction history and statistics
            update_data = {
                "$push": {"transactions": transaction},
                "$set": {"last_updated": datetime.now()}
            }
            
            # Update statistics based on transaction type
            if tx_data.get("type") == "swap":
                if tx_data.get("source") == "signal":
                    update_data["$inc"] = {
                        "total_swaps_signal": 1,
                        "total_volume_sol": float(tx_data.get("input_amount", 0))
                    }
                else:
                    update_data["$inc"] = {
                        "total_swaps_user": 1,
                        "total_volume_sol": float(tx_data.get("input_amount", 0))
                    }
            elif tx_data.get("type") == "transfer":
                update_data["$inc"] = {"total_payments_sent": 1}
            elif tx_data.get("type") == "receive":
                update_data["$inc"] = {"total_payments_received": 1}
            
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                update_data
            )
            
            return result.modified_count > 0
            
        except PyMongoError as e:
            logger.error(f"Add transaction error: {e}")
            return False
    
    def increment_call_stats(self, telegram_id: int, responded: bool = False) -> bool:
        """Increment call statistics"""
        try:
            update_data = {
                "$inc": {"total_calls": 1},
                "$set": {"last_updated": datetime.now()}
            }
            
            if responded:
                update_data["$inc"]["total_calls_responded"] = 1
            
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                update_data
            )
            
            return result.modified_count > 0
            
        except PyMongoError as e:
            logger.error(f"Increment call stats error: {e}")
            return False
    
    def get_user_statistics(self, telegram_id: int) -> Dict:
        """Get comprehensive user statistics"""
        user = self.get_user(telegram_id)
        if not user:
            return {}
        
        return {
            "total_calls": user.get("total_calls", 0),
            "total_calls_responded": user.get("total_calls_responded", 0),
            "response_rate": (user.get("total_calls_responded", 0) / user.get("total_calls", 1)) * 100 if user.get("total_calls", 0) > 0 else 0,
            "total_swaps_signal": user.get("total_swaps_signal", 0),
            "total_swaps_user": user.get("total_swaps_user", 0),
            "total_swaps": user.get("total_swaps_signal", 0) + user.get("total_swaps_user", 0),
            "total_volume_sol": user.get("total_volume_sol", 0),
            "total_payments_sent": user.get("total_payments_sent", 0),
            "total_payments_received": user.get("total_payments_received", 0),
            "active_channels": len(user.get("active_channels", [])),
            "total_signals": len(user.get("signal_history", [])),
            "member_since": user.get("created_at")
        }
    
    # ==================== PHONE & SETTINGS OPERATIONS ====================
    
    def set_phone_number(self, telegram_id: int, phone_number: str, verified: bool = False) -> bool:
        """Set user's phone number with verification status"""
        try:
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": {
                    "phone_number": phone_number,
                    "phone_verified": verified,  # Now controlled by parameter
                    "last_updated": datetime.now()
                }}
            )
            
            return result.modified_count > 0
            
        except PyMongoError as e:
            logger.error(f"Set phone error: {e}")
            return False
        
    def set_phone_pending(self, telegram_id: int, phone_number: str) -> bool:
        """Set phone number as pending verification (NOT verified yet)"""
        try:
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": {
                    "phone_number": phone_number,
                    "phone_verified": False,  # NOT verified until OTP confirmed
                    "last_updated": datetime.now()
                }}
            )
            
            return result.modified_count > 0
            
        except PyMongoError as e:
            logger.error(f"Set phone pending error: {e}")
            return False
        
    def verify_phone_number(self, telegram_id: int) -> bool:
        """Mark phone number as verified after OTP confirmation"""
        try:
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": {
                    "phone_verified": True,
                    "last_updated": datetime.now()
                }}
            )
            
            logger.info(f"Phone verified for user {telegram_id}")
            return result.modified_count > 0
            
        except PyMongoError as e:
            logger.error(f"Verify phone error: {e}")
            return False
    
    def toggle_calls(self, telegram_id: int, enabled: bool) -> bool:
        """Toggle global call setting and sync all channels"""
        try:
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {
                    "$set": {
                        "calls_enabled": enabled,
                        "last_updated": datetime.now()
                    }
                }
            )
            
            self.users.update_one(
                {"telegram_id": telegram_id},
                {
                    "$set": {"active_channels.$[].calls_enabled": enabled}
                }
            )
            
            return result.matched_count > 0
            
        except PyMongoError as e:
            logger.error(f"Toggle calls error: {e}")
            return False
    
    def update_trading_settings(self, telegram_id: int, settings: Dict) -> bool:
        """Update user trading settings"""
        try:
            update_data = {"last_updated": datetime.now()}
            
            if "slippage_percent" in settings:
                update_data["slippage_percent"] = settings["slippage_percent"]
            if "max_trade_amount_sol" in settings:
                update_data["max_trade_amount_sol"] = settings["max_trade_amount_sol"]
            
            result = self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": update_data}
            )
            
            return result.modified_count > 0
            
        except PyMongoError as e:
            logger.error(f"Update settings error: {e}")
            return False