"""
Cortex Unified Bot - Encryption Service
Handles private key encryption and decryption
"""

import logging
import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

from config import ENCRYPTION_KEY

logger = logging.getLogger(__name__)


class EncryptionManager:
    """Manages encryption for sensitive data"""
    
    def __init__(self):
        """Initialize encryption manager"""
        if not ENCRYPTION_KEY:
            raise ValueError("ENCRYPTION_KEY not configured")
        
        self.master_key = ENCRYPTION_KEY.encode()
        logger.info("Encryption manager initialized")
    
    def derive_key_from_id(self, user_id: int) -> Fernet:
        """Derive unique encryption key for user"""
        # Create salt from user ID
        salt = hashlib.sha256(str(user_id).encode()).digest()
        
        # Derive key using PBKDF2
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        
        key = base64.urlsafe_b64encode(kdf.derive(self.master_key))
        return Fernet(key)
    
    def encrypt_private_key(self, private_key: str, user_id: int = 0) -> str:
        """Encrypt a private key"""
        try:
            # Get user-specific cipher
            cipher = self.derive_key_from_id(user_id)
            
            # Encrypt
            encrypted = cipher.encrypt(private_key.encode())
            
            # Return as string
            return encrypted.decode()
        
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise
    
    def decrypt_private_key(self, encrypted_key: str, user_id: int = 0) -> str:
        """Decrypt a private key"""
        try:
            # Get user-specific cipher
            cipher = self.derive_key_from_id(user_id)
            
            # Decrypt
            decrypted = cipher.decrypt(encrypted_key.encode())
            
            # Return as string
            return decrypted.decode()
        
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise
    
    def generate_encryption_key(self) -> str:
        """Generate a new encryption key"""
        return Fernet.generate_key().decode()
    
    def encrypt_data(self, data: str, user_id: int = 0) -> str:
        """Encrypt any string data"""
        try:
            cipher = self.derive_key_from_id(user_id)
            encrypted = cipher.encrypt(data.encode())
            return encrypted.decode()
        
        except Exception as e:
            logger.error(f"Data encryption error: {e}")
            raise
    
    def decrypt_data(self, encrypted_data: str, user_id: int = 0) -> str:
        """Decrypt any string data"""
        try:
            cipher = self.derive_key_from_id(user_id)
            decrypted = cipher.decrypt(encrypted_data.encode())
            return decrypted.decode()
        
        except Exception as e:
            logger.error(f"Data decryption error: {e}")
            raise