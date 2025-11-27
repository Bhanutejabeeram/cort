"""
Cortex Unified Bot - Jupiter Swap Service
Handles all Jupiter API interactions for token swaps
"""

import logging
import requests
import base64
from typing import Dict, Optional, List

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from config import JUPITER_BASE_URL, JUPITER_API_KEY, SOL_MINT

logger = logging.getLogger(__name__)

class NullSigner:
    """Dummy signer for gasless transactions where other signers are handled by Jupiter"""
    def __init__(self, pubkey):
        self.pubkey = pubkey

class JupiterAPI:
    """Jupiter API interface for swaps"""
    
    def __init__(self):
        """Initialize Jupiter API"""
        self.base_url = JUPITER_BASE_URL
        self.api_key = JUPITER_API_KEY
        self.headers = {
            "Content-Type": "application/json"
        }
        
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"
        
        logger.info(f"Jupiter API initialized with base URL: {self.base_url}")
    
    def search_tokens(self, query: str) -> List[Dict]:
        """Search for tokens by name or symbol"""
        try:
            url = f"{self.base_url}/search"
            params = {"query": query}
            
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            tokens = response.json()
            return tokens if isinstance(tokens, list) else []
        
        except Exception as e:
            logger.error(f"Token search error: {e}")
            return []
    
    async def get_token_info(self, mint_address: str) -> Optional[Dict]:
        """Get token information by mint address"""
        try:
            # Search for token by address
            tokens = self.search_tokens(mint_address)
            
            if tokens and len(tokens) > 0:
                return tokens[0]
            
            # For Solana native token
            if mint_address == SOL_MINT:
                return {
                    "symbol": "SOL",
                    "name": "Solana",
                    "id": SOL_MINT,
                    "decimals": 9
                }
            
            # Try direct lookup (fallback)
            url = f"{self.base_url}/tokens/{mint_address}"
            response = requests.get(url, headers=self.headers, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            
            # Default fallback
            return {
                "id": mint_address,
                "symbol": "Unknown",
                "name": "Unknown Token",
                "decimals": 6
            }
        
        except Exception as e:
            logger.error(f"Get token info error: {e}")
            return None
    
    def get_wallet_balances(self, wallet_address: str) -> List[Dict]:
        """Get all token balances for a wallet"""
        try:
            url = f"{self.base_url}/balances"
            params = {"wallet": wallet_address}
            
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            balances = response.json()
            
            # Format balances
            formatted = []
            for balance in balances:
                formatted.append({
                    "token": balance.get("symbol", "Unknown"),
                    "mint": balance.get("mint"),
                    "amount": balance.get("amount", 0),
                    "decimals": balance.get("decimals", 0),
                    "usd_value": balance.get("usdValue", 0)
                })
            
            return formatted
        
        except Exception as e:
            logger.error(f"Get balances error: {e}")
            return []
    
    def get_swap_order(self, input_mint: str, output_mint: str, amount: int,
                      slippage_bps: int = 500, taker_address: str = None) -> Optional[Dict]:
        """
        Get swap order from Jupiter /order endpoint
        
        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount: Amount in smallest units (lamports/base units)
            slippage_bps: Slippage in basis points
            taker_address: Optional wallet address to get transaction
        
        Returns:
            Dict with quote and optionally transaction
        """
        try:
            # Resolve token symbols to mint addresses
            input_mint = self._resolve_token_mint(input_mint)
            output_mint = self._resolve_token_mint(output_mint)
            
            url = f"{self.base_url}/order"
            
            # Build query parameters
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": slippage_bps
            }
            
            # Add taker if provided (for execution)
            if taker_address:
                params["taker"] = taker_address
                logger.info(f"[JUPITER] Getting order with taker: {taker_address[:8]}...")
            else:
                logger.info(f"[JUPITER] Getting quote (no taker)")
            
            # Use GET request with query parameters
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            logger.info(f"[JUPITER] Order response: outAmount={data.get('outAmount')}, transaction={'present' if data.get('transaction') else 'null'}")
            
            return data
        
        except Exception as e:
            logger.error(f"[JUPITER] Get order error: {e}")
            return None
        
    def execute_jupiter_swap(self, signed_transaction: str, request_id: str) -> Dict:
        """Execute signed swap via Jupiter /execute endpoint"""
        try:
            url = f"{self.base_url}/execute"
            
            payload = {
                "signedTransaction": signed_transaction,
                "requestId": request_id
            }
            
            logger.info(f"[JUPITER] Sending to /execute with requestId: {request_id}")
            
            response = requests.post(url, json=payload, headers=self.headers, timeout=60)
            
            if response.status_code == 200:
                data = response.json()
                
                # Jupiter returns result directly (not nested under "result" key)
                signature = data.get("signature")
                
                if signature:
                    logger.info(f"[JUPITER] Execution successful: {signature}")
                    return {
                        "success": True,
                        "result": data  # Return entire response as result
                    }
                else:
                    logger.error(f"[JUPITER] No signature in response: {data}")
                    return {
                        "success": False,
                        "error": "No signature returned from Jupiter"
                    }
            else:
                error_text = response.text
                logger.error(f"[JUPITER] Execute failed ({response.status_code}): {error_text}")
                return {
                    "success": False,
                    "error": f"Jupiter execution failed: {error_text}"
                }
            
        except Exception as e:
            logger.error(f"[JUPITER] Execute error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def execute_swap(self, wallet_address: str, private_key: str,
                      input_token: str, output_token: str,
                      amount: str, slippage_percent: float = 5) -> Dict:
        """Execute a token swap using Jupiter Ultra API"""
        try:
            logger.info(f"[JUPITER] Executing swap: {amount} {input_token[:8]}... → {output_token[:8]}...")
            
            # Convert amount to lamports/smallest unit
            input_mint = self._resolve_token_mint(input_token)
            output_mint = self._resolve_token_mint(output_token)
            
            # Get decimals for input token
            decimals = 9 if input_mint == SOL_MINT else 6
            amount_smallest = int(float(amount) * (10 ** decimals))
            
            # Get order with transaction (includes taker address)
            slippage_bps = int(slippage_percent * 100)
            
            logger.info(f"[JUPITER] Getting order with transaction...")
            order_data = self.get_swap_order(
                input_mint=input_mint,
                output_mint=output_mint,
                amount=amount_smallest,
                slippage_bps=slippage_bps,
                taker_address=wallet_address
            )
            
            if not order_data:
                logger.error(f"[JUPITER] Failed to get order")
                return {"success": False, "error": "Failed to get order from Jupiter"}
            
            # Check if transaction was returned
            unsigned_tx = order_data.get("transaction")
            if not unsigned_tx:
                logger.error(f"[JUPITER] No transaction in response")
                return {"success": False, "error": "No transaction returned. Taker address may be invalid."}
            
            # Get request ID (needed for execution)
            request_id = order_data.get("requestId")
            if not request_id:
                logger.error(f"[JUPITER] No requestId in response")
                return {"success": False, "error": "No request ID returned from Jupiter"}
            
            logger.info(f"[JUPITER] Order received with requestId: {request_id}")
            logger.info(f"[JUPITER] Signing transaction...")
            
            # Sign transaction
            signed_tx = self.sign_transaction_secure(unsigned_tx, private_key, wallet_address)
            
            if not signed_tx:
                logger.error(f"[JUPITER] Failed to sign transaction")
                return {"success": False, "error": "Failed to sign transaction"}
            
            logger.info(f"[JUPITER] Transaction signed, executing via Jupiter...")
            
            # Execute via Jupiter (not directly to blockchain!)
            execution_result = self.execute_jupiter_swap(signed_tx, request_id)
            
            if not execution_result.get("success"):
                error_msg = execution_result.get("error", "Execution failed")
                logger.error(f"[JUPITER] Execution failed: {error_msg}")
                return {"success": False, "error": error_msg}
            
            # Get result details
            result = execution_result.get("result", {})
            signature = result.get("signature")
            
            logger.info(f"[JUPITER] Swap successful! Signature: {signature}")
            
            # Calculate output amount
            output_amount_raw = result.get("outputAmountResult") or order_data.get("outAmount", 0)
            output_decimals = 9 if output_mint == SOL_MINT else 6
            output_amount = int(output_amount_raw) / (10 ** output_decimals)
            
            return {
                "success": True,
                "signature": signature,
                "output_amount": output_amount,
                "slot": result.get("slot"),
                "status": result.get("status")
            }
        
        except Exception as e:
            logger.error(f"[JUPITER] Execute swap error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def sign_transaction_secure(self, unsigned_tx_base64: str, private_key_str: str, 
                           wallet_address: str) -> Optional[str]:
        """Sign transaction with support for gasless swaps"""
        try:
            import base58
            from solders.keypair import Keypair
            from solders.transaction import VersionedTransaction
            from solders.pubkey import Pubkey
            
            # Decode private key
            if len(private_key_str) == 64:  # Hex seed
                seed = bytes.fromhex(private_key_str)
                keypair = Keypair.from_seed(seed)
            else:  # Base58 keypair
                keypair_bytes = base58.b58decode(private_key_str)
                keypair = Keypair.from_bytes(keypair_bytes)
            
            # Decode unsigned transaction
            unsigned_tx_bytes = base64.b64decode(unsigned_tx_base64)
            raw_tx = VersionedTransaction.from_bytes(unsigned_tx_bytes)
            message = raw_tx.message
            
            logger.info(f"[JUPITER] Transaction has {len(message.account_keys)} account keys")
            
            # Find wallet position in account keys
            your_wallet_pubkey = keypair.pubkey()
            wallet_position = None
            
            for i, account_key in enumerate(message.account_keys):
                if account_key == your_wallet_pubkey:
                    wallet_position = i
                    logger.info(f"[JUPITER] Wallet found at position {i}")
                    break
            
            if wallet_position is None:
                logger.error(f"[JUPITER] Wallet not found in transaction!")
                return None
            
            # Check if gasless (wallet is not first signer)
            is_gasless = (wallet_position != 0)
            
            if is_gasless:
                logger.info(f"[JUPITER] Gasless transaction detected")
                
                # Create signers list with NullSigners for other positions
                signers = []
                for i in range(len(message.account_keys)):
                    if i == wallet_position:
                        signers.append(keypair)
                    elif i < message.header.num_required_signatures:
                        signers.append(NullSigner(message.account_keys[i]))
                
                signed_tx = VersionedTransaction(message, signers)
            else:
                logger.info(f"[JUPITER] Standard transaction")
                signed_tx = VersionedTransaction(message, [keypair])
            
            # Serialize to base64
            signed_tx_bytes = bytes(signed_tx)
            signed_tx_base64 = base64.b64encode(signed_tx_bytes).decode('utf-8')
            
            logger.info(f"[JUPITER] Transaction signed successfully")
            return signed_tx_base64
            
        except Exception as e:
            logger.error(f"[JUPITER] Sign transaction error: {e}", exc_info=True)
            return None
    
    def _resolve_token_mint(self, token: str) -> str:
        """Resolve token symbol to mint address"""
        # Check if already a mint address
        if len(token) > 20:
            return token
        
        # Common tokens
        token_upper = token.upper()
        if token_upper == "SOL":
            return SOL_MINT
        elif token_upper == "USDC":
            return "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        elif token_upper == "USDT":
            return "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
        
        return token