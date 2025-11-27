"""
Cortex Unified Bot - Alchemy Transfer Service
Handles Solana blockchain transfers via Alchemy API
"""

import logging
import requests
import base58
import time
from typing import Dict, Optional
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.message import Message
from solders.hash import Hash
from solders.system_program import transfer, TransferParams
from solders.instruction import Instruction, AccountMeta

from config import (
    ALCHEMY_API_KEY,
    ALCHEMY_SOLANA_RPC,
    TOKEN_PROGRAM_ID,
    ASSOCIATED_TOKEN_PROGRAM_ID
)

logger = logging.getLogger(__name__)


class AlchemyTransfer:
    """Handles Solana transfers via Alchemy API"""
    
    def __init__(self):
        """Initialize Alchemy transfer service"""
        self.rpc_url = ALCHEMY_SOLANA_RPC
        self.token_program_id = Pubkey.from_string(TOKEN_PROGRAM_ID)
        self.ata_program_id = Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM_ID)
        logger.info("Alchemy transfer service initialized")
    
    def execute_sol_transfer(self, private_key: str, recipient_address: str, 
                            amount_sol: float) -> Dict:
        """Execute SOL transfer"""
        try:
            logger.info(f"Starting SOL transfer: {amount_sol} SOL to {recipient_address}")
            
            # Step 1: Prepare keypair
            private_key_bytes = base58.b58decode(private_key)
            keypair = Keypair.from_bytes(private_key_bytes)
            sender_pubkey = keypair.pubkey()
            recipient_pubkey = Pubkey.from_string(recipient_address)
            
            logger.info(f"Sender: {str(sender_pubkey)}")
            
            # Step 2: Convert SOL to lamports (1 SOL = 1,000,000,000 lamports)
            amount_lamports = int(amount_sol * 1_000_000_000)
            logger.info(f"Amount in lamports: {amount_lamports}")
            
            # Step 3: Get recent blockhash
            blockhash_response = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getLatestBlockhash",
                    "params": [{"commitment": "finalized"}]
                },
                timeout=10
            )
            
            blockhash_data = blockhash_response.json()
            if "error" in blockhash_data:
                return {"success": False, "error": f"Failed to get blockhash: {blockhash_data['error']}"}
            
            blockhash_str = blockhash_data["result"]["value"]["blockhash"]
            recent_blockhash = Hash.from_string(blockhash_str)
            logger.info(f"Recent blockhash: {blockhash_str}")
            
            # Step 4: Create transfer instruction
            transfer_ix = transfer(
                TransferParams(
                    from_pubkey=sender_pubkey,
                    to_pubkey=recipient_pubkey,
                    lamports=amount_lamports
                )
            )
            
            # Step 5: Build and sign transaction
            message = Message.new_with_blockhash(
                [transfer_ix],
                sender_pubkey,
                recent_blockhash
            )
            
            transaction = Transaction([keypair], message, recent_blockhash)
            
            # Step 6: Serialize and encode
            serialized_tx = bytes(transaction)
            encoded_tx = base58.b58encode(serialized_tx).decode('utf-8')
            
            # Step 7: Send transaction
            logger.info("Sending transaction to blockchain...")
            send_response = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        encoded_tx,
                        {"encoding": "base58", "skipPreflight": False}
                    ]
                },
                timeout=30
            )
            
            send_data = send_response.json()
            
            if "error" in send_data:
                error_msg = send_data["error"].get("message", "Unknown error")
                logger.error(f"Transaction failed: {error_msg}")
                return {"success": False, "error": error_msg}
            
            signature = send_data["result"]
            logger.info(f"Transaction sent successfully: {signature}")
            
            return {
                "success": True,
                "signature": signature
            }
            
        except Exception as e:
            logger.error(f"SOL transfer error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def execute_spl_transfer(self, private_key: str, recipient_address: str,
                            token_mint: str, amount: float, decimals: int = 6) -> Dict:
        """Execute SPL token transfer (USDC/USDT)"""
        try:
            logger.info(f"Starting SPL transfer: {amount} tokens to {recipient_address}")
            
            # Step 1: Prepare keypair
            private_key_bytes = base58.b58decode(private_key)
            keypair = Keypair.from_bytes(private_key_bytes)
            sender_pubkey = keypair.pubkey()
            recipient_pubkey = Pubkey.from_string(recipient_address)
            mint_pubkey = Pubkey.from_string(token_mint)
            
            # Step 2: Get Associated Token Accounts
            sender_ata = self.get_associated_token_address(sender_pubkey, mint_pubkey)
            recipient_ata = self.get_associated_token_address(recipient_pubkey, mint_pubkey)
            
            logger.info(f"Sender ATA: {str(sender_ata)}")
            logger.info(f"Recipient ATA: {str(recipient_ata)}")
            
            # Step 3: Check if recipient ATA exists
            recipient_ata_exists = self.check_account_exists(str(recipient_ata))
            
            instructions = []
            
            # Step 4: Create ATA if needed
            if not recipient_ata_exists:
                logger.info("Creating Associated Token Account for recipient...")
                create_ata_ix = self.create_associated_token_account_instruction(
                    sender_pubkey,
                    recipient_pubkey,
                    mint_pubkey
                )
                instructions.append(create_ata_ix)
            
            # Step 5: Create transfer instruction
            amount_base = int(amount * (10 ** decimals))
            
            transfer_ix = Instruction(
                program_id=self.token_program_id,
                accounts=[
                    AccountMeta(pubkey=sender_ata, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=recipient_ata, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=sender_pubkey, is_signer=True, is_writable=False),
                ],
                data=bytes([3]) + amount_base.to_bytes(8, 'little')  # Transfer instruction
            )
            instructions.append(transfer_ix)
            
            # Step 6: Get recent blockhash
            blockhash_response = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getLatestBlockhash",
                    "params": [{"commitment": "finalized"}]
                },
                timeout=10
            )
            
            blockhash_data = blockhash_response.json()
            if "error" in blockhash_data:
                return {"success": False, "error": f"Failed to get blockhash: {blockhash_data['error']}"}
            
            blockhash_str = blockhash_data["result"]["value"]["blockhash"]
            recent_blockhash = Hash.from_string(blockhash_str)
            
            # Step 7: Build and sign transaction
            message = Message.new_with_blockhash(
                instructions,
                sender_pubkey,
                recent_blockhash
            )
            
            transaction = Transaction([keypair], message, recent_blockhash)
            
            # Step 8: Send transaction
            serialized_tx = bytes(transaction)
            encoded_tx = base58.b58encode(serialized_tx).decode('utf-8')
            
            send_response = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        encoded_tx,
                        {"encoding": "base58", "skipPreflight": False}
                    ]
                },
                timeout=30
            )
            
            send_data = send_response.json()
            
            if "error" in send_data:
                error_msg = send_data["error"].get("message", "Unknown error")
                logger.error(f"SPL transfer failed: {error_msg}")
                return {"success": False, "error": error_msg}
            
            signature = send_data["result"]
            logger.info(f"SPL transfer successful: {signature}")
            
            return {
                "success": True,
                "signature": signature
            }
            
        except Exception as e:
            logger.error(f"SPL transfer error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        
    def get_sol_balance(self, wallet_address: str) -> float:
        """Get SOL balance for wallet (used for validation)"""
        try:
            response = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [wallet_address]
                },
                timeout=10
            )
            
            data = response.json()
            if "error" in data:
                logger.warning(f"Error getting balance: {data['error']}")
                return 0.0
            
            lamports = data.get("result", {}).get("value", 0)
            sol_balance = lamports / 1_000_000_000
            
            logger.info(f"[BALANCE CHECK] {wallet_address[:8]}... has {sol_balance} SOL")
            return sol_balance
            
        except Exception as e:
            logger.error(f"Error getting SOL balance: {e}")
            return 0.0
    
    def get_transaction_status(self, signature: str) -> str:
        """Check transaction status"""
        try:
            response = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignatureStatuses",
                    "params": [[signature]]
                },
                timeout=10
            )
            
            data = response.json()
            
            if "error" in data:
                return "error"
            
            result = data.get("result", {}).get("value", [])
            if not result or result[0] is None:
                return "pending"
            
            status_info = result[0]
            
            if status_info.get("err"):
                return "failed"
            
            if status_info.get("confirmationStatus") in ["confirmed", "finalized"]:
                return "confirmed"
            
            return "pending"
            
        except Exception as e:
            logger.error(f"Error checking transaction status: {e}")
            return "error"
    
    def check_account_exists(self, account_address: str) -> bool:
        """Check if Solana account exists"""
        try:
            response = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [account_address, {"encoding": "base64"}]
                },
                timeout=10
            )
            
            data = response.json()
            return data.get("result", {}).get("value") is not None
            
        except Exception as e:
            logger.error(f"Error checking account: {e}")
            return False
    
    def get_associated_token_address(self, owner_pubkey: Pubkey, mint_pubkey: Pubkey) -> Pubkey:
        """Calculate Associated Token Account address"""
        # Standard ATA derivation
        seeds = [
            bytes(owner_pubkey),
            bytes(self.token_program_id),
            bytes(mint_pubkey)
        ]
        
        ata_pubkey, _ = Pubkey.find_program_address(seeds, self.ata_program_id)
        return ata_pubkey
    
    def create_associated_token_account_instruction(self, payer: Pubkey, 
                                                    owner: Pubkey, mint: Pubkey) -> Instruction:
        """Create instruction for Associated Token Account creation"""
        ata = self.get_associated_token_address(owner, mint)
        
        return Instruction(
            program_id=self.ata_program_id,
            accounts=[
                AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
                AccountMeta(pubkey=ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner, is_signer=False, is_writable=False),
                AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
                AccountMeta(pubkey=Pubkey.from_string("11111111111111111111111111111111"), is_signer=False, is_writable=False),
                AccountMeta(pubkey=self.token_program_id, is_signer=False, is_writable=False),
            ],
            data=bytes([])
        )


# Global instance
alchemy_transfer = AlchemyTransfer()