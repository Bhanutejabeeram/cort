"""
Cortex Unified Bot - Wallet Manager
Handles Solana wallet creation and imports
"""

import logging
import base58
from typing import Dict, Optional

from solders.keypair import Keypair
from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)


class WalletManager:
    """Manages Solana wallets"""
    
    def __init__(self):
        """Initialize wallet manager"""
        logger.info("Wallet manager initialized")
    
    def create_new_wallet(self) -> Dict:
        """Create a new Solana wallet"""
        try:
            # Generate new keypair
            keypair = Keypair()
            
            # Get public key (address)
            public_key = str(keypair.pubkey())
            
            # Get private key in base58 format
            private_key = base58.b58encode(bytes(keypair)).decode('utf-8')
            
            logger.info(f"New wallet created: {public_key}")
            
            return {
                "success": True,
                "address": public_key,
                "private_key": private_key
            }
        
        except Exception as e:
            logger.error(f"Create wallet error: {e}")
            return {"success": False, "error": str(e)}
    
    def import_from_private_key(self, private_key: str) -> Dict:
        """Import wallet from private key"""
        try:
            # Clean the private key
            private_key = private_key.strip()
            
            # Decode from base58
            try:
                decoded = base58.b58decode(private_key)
            except:
                # Try as hex
                try:
                    decoded = bytes.fromhex(private_key)
                except:
                    return {"success": False, "error": "Invalid private key format"}
            
            # Validate length
            if len(decoded) != 64:
                return {"success": False, "error": "Invalid private key length"}
            
            # Create keypair
            keypair = Keypair.from_bytes(decoded)
            
            # Get public key
            public_key = str(keypair.pubkey())
            
            logger.info(f"Wallet imported: {public_key}")
            
            return {
                "success": True,
                "address": public_key,
                "private_key": private_key if len(private_key) > 64 else base58.b58encode(decoded).decode()
            }
        
        except Exception as e:
            logger.error(f"Import private key error: {e}")
            return {"success": False, "error": f"Import failed: {str(e)}"}
    
    def import_from_mnemonic(self, mnemonic: str) -> Dict:
        """Import wallet from mnemonic phrase"""
        try:
            # Check for mnemonic library
            try:
                from mnemonic import Mnemonic
            except ImportError:
                return {
                    "success": False,
                    "error": "Mnemonic import not available. Install 'mnemonic' package."
                }
            
            # Validate mnemonic
            words = mnemonic.strip().split()
            
            if len(words) not in [12, 24]:
                return {"success": False, "error": "Recovery phrase must be 12 or 24 words"}
            
            # Create mnemo instance
            mnemo = Mnemonic("english")
            
            # Validate words
            if not mnemo.check(mnemonic):
                return {"success": False, "error": "Invalid recovery phrase"}
            
            # Generate seed from mnemonic
            seed = mnemo.to_seed(mnemonic)
            
            # Create keypair from seed (using first 32 bytes)
            keypair = Keypair.from_seed(seed[:32])
            
            # Get keys
            public_key = str(keypair.pubkey())
            private_key = base58.b58encode(bytes(keypair)).decode('utf-8')
            
            logger.info(f"Wallet imported from mnemonic: {public_key}")
            
            return {
                "success": True,
                "address": public_key,
                "private_key": private_key
            }
        
        except Exception as e:
            logger.error(f"Import mnemonic error: {e}")
            return {"success": False, "error": f"Import failed: {str(e)}"}
    
    def validate_address(self, address: str) -> bool:
        """Validate if address is valid Solana address"""
        try:
            # Check length
            if len(address) < 32 or len(address) > 44:
                return False
            
            # Try to create Pubkey
            Pubkey.from_string(address)
            return True
        
        except Exception:
            return False
    
    def get_keypair_from_private_key(self, private_key: str) -> Optional[Keypair]:
        """Get Keypair object from private key string"""
        try:
            # Decode private key
            if len(private_key) == 64:  # Hex
                decoded = bytes.fromhex(private_key)
            else:  # Base58
                decoded = base58.b58decode(private_key)
            
            return Keypair.from_bytes(decoded)
        
        except Exception as e:
            logger.error(f"Get keypair error: {e}")
            return None