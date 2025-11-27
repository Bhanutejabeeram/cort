"""
Cortex Unified Bot - AI Handler
OpenAI Responses API Integration with all tools from both Part 1 and Part 2
"""

import json
import logging
import requests
import uuid
import asyncio  # FIXED: Added missing import
import html
import re
from typing import Dict, Tuple, Optional, Any, List
from datetime import datetime, timezone, timedelta
from openai import OpenAI

from services.alchemy_transfer import alchemy_transfer
from config import (
    BASE_TRANSACTION_FEE,
    RENT_EXEMPTION_FEE,
    ATA_CREATION_FEE,
    MINIMUM_SOL_TRANSFER_NEW_USER
)
import hashlib

from config import (
    OPENAI_API_KEY, OPENAI_MODEL,
    JUPITER_BASE_URL, JUPITER_API_KEY, ALCHEMY_API_KEY,
    SOL_MINT, USDC_MINT, USDT_MINT,
    SUPPORTED_PAYMENT_TOKENS
)
from prompts.ai_prompts import get_system_prompt
from services.jupiter_swap import JupiterAPI
from services.wallet_manager import WalletManager
from services.encryption import EncryptionManager
from monitoring.channel_monitor import channel_monitor_instance
from prompts.ai_prompts import get_context_guide


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO date string to datetime object (naive, no timezone)"""
    if not date_str:
        return None
    try:
        if 'T' in date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.replace(tzinfo=None)
        else:
            return datetime.strptime(date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None


def _to_naive_datetime(dt) -> Optional[datetime]:
    """Convert any datetime to naive datetime for comparison"""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except:
            return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _format_datetime_utc(dt) -> str:
    """Format datetime as '15 Jan 2025, 14:30 UTC'"""
    if not dt:
        return "Unknown"
    try:
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt.strftime('%d %b %Y, %H:%M UTC')
    except:
        return str(dt)

logger = logging.getLogger(__name__)


class AIHandler:
    """Handles all AI interactions using OpenAI Responses API"""
    
    SUPPORTED_PAYMENT_TOKENS = {
        "SOL": {
            "mint": SOL_MINT,
            "decimals": 9,
            "symbol": "SOL"
        },
        "USDC": {
            "mint": USDC_MINT,
            "decimals": 6,
            "symbol": "USDC"
        },
        "USDT": {
            "mint": USDT_MINT,
            "decimals": 6,
            "symbol": "USDT"
        }
    }

    def __init__(self, database):
        """Initialize AI handler with OpenAI client"""
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.db = database
        self.jupiter = JupiterAPI()
        self.wallet_manager = WalletManager()
        self.encryption = EncryptionManager()
        self.jupiter_base_url = JUPITER_BASE_URL
        self.alchemy_api_key = ALCHEMY_API_KEY
        
        # Get system instructions
        self.system_instructions = get_system_prompt()
        
        # Store the event loop reference
        self._loop = None
        
        logger.info("AI Handler initialized with Responses API")
    
    def set_event_loop(self, loop):
        """Set the event loop reference for async operations"""
        self._loop = loop
    
    # ==================== TOOL DEFINITIONS ====================
    
    def get_tools(self):
        """Return list of available tools for OpenAI Responses API"""
        return [
            {
                "type": "function",
                "name": "get_bot_info",
                "description": "Get information about Cortexa bot - its name, capabilities, features, and how to use it. Call this when user asks: 'What's your name?', 'Who are you?', 'What can you do?', 'Tell me about yourself', 'What are your abilities?', 'How do I use you?', 'Help', 'What is Cortexa?'",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query_type": {
                            "type": "string",
                            "enum": ["name", "about", "capabilities", "how_to_use", "all"],
                            "description": "What info to return: 'name' for just the name, 'about' for description, 'capabilities' for features list, 'how_to_use' for usage guide, 'all' for everything"
                        }
                    },
                    "required": ["query_type"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "search_token_tool",
                "description": (
                    "Search for Solana tokens by name, symbol, or mint address. "
                    "Returns comprehensive data including: price (USD), mint/contract address, market cap, "
                    "liquidity, 24h volume, holder count, price changes (24h and 7d), verification status. "
                    "Returns the HIGHEST market cap token matching the query. "
                    "Use when users ask: 'Tell me about [token]', 'What is [token]?', '[token] price', "
                    "'Search for [token]', 'Info on [token]', '[token] details', 'price of [token]', "
                    "'[token] contract address', '[token] market cap'. "
                    "This provides TOKEN INFORMATION ONLY - not swap previews or quotes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Token name, symbol, or contract address to search for"
                        }
                    },
                    "required": ["query"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "get_swap_preview_tool",
                "description": (
                    "Get a detailed swap PREVIEW with quote for swapping Solana tokens. "
                    "Shows expected output amount, price impact, and route before execution. "
                    "User must confirm via button to execute the swap. "
                    "CRITICAL: Only accepts FULL CONTRACT ADDRESSES (32-44 character base58 strings). "
                    "Exception: 'SOL' for native Solana. "
                    "If user provides token name/symbol instead of address, DO NOT call this tool. "
                    "Instead respond asking for the contract address."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input_token": {
                            "type": "string",
                            "description": "INPUT token contract address or 'SOL' for native Solana"
                        },
                        "output_token": {
                            "type": "string",
                            "description": "OUTPUT token contract address or 'SOL' for native Solana"
                        },
                        "amount": {
                            "type": "string",
                            "description": "Amount of input_token to swap as string (e.g., '1', '0.5')"
                        }
                    },
                    "required": ["input_token", "output_token", "amount"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "get_swap_history",
                "description": (
                    "Get user's TOKEN SWAP/TRADE history. Returns trades where user exchanged one token for another. "
                    "Supports filtering by date range. "
                    "Use when user asks: 'swap history', 'my swaps', 'trade history', 'my trades', "
                    "'swaps from last week', 'trades before January', 'show my last 5 swaps'. "
                    "DO NOT use for payments/transfers between users - use get_transfer_history for that."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of swaps to retrieve. Default 10 if not specified."
                        },
                        "before_date": {
                            "type": "string",
                            "description": "ISO date string (YYYY-MM-DD). Get swaps BEFORE this date. Optional - omit if not filtering by date."
                        },
                        "after_date": {
                            "type": "string",
                            "description": "ISO date string (YYYY-MM-DD). Get swaps AFTER this date. Optional - omit if not filtering by date."
                        }
                    },
                    "required": ["limit"],
                    "additionalProperties": False
                },
                "strict": False
            },
            {
                "type": "function",
                "name": "get_transfer_history",
                "description": (
                    "Get user's PAYMENT/TRANSFER history. Returns payments sent to or received from other users. "
                    "Supports filtering by: date range, specific username, direction (sent/received). "
                    "Use when user asks: 'transfer history', 'payment history', 'payments to @alice', "
                    "'transfers from @bob', 'what did I send last month', 'payments I received'. "
                    "DO NOT use for token swaps/trades - use get_swap_history for that."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of transfers to retrieve. Default 10 if not specified."
                        },
                        "before_date": {
                            "type": "string",
                            "description": "ISO date string (YYYY-MM-DD). Get transfers BEFORE this date. Optional."
                        },
                        "after_date": {
                            "type": "string",
                            "description": "ISO date string (YYYY-MM-DD). Get transfers AFTER this date. Optional."
                        },
                        "username": {
                            "type": "string",
                            "description": "Filter by specific username (without @). Shows transfers to/from this user. Optional."
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["sent", "received", "all"],
                            "description": "Filter by direction: 'sent' for outgoing, 'received' for incoming, 'all' for both. Default 'all'."
                        }
                    },
                    "required": ["limit"],
                    "additionalProperties": False
                },
                "strict": False
            },
            {
                "type": "function",
                "name": "display_user_wallet",
                "description": (
                    "Show the user's connected Solana wallet address, wallet type (created/imported), and creation date. "
                    "Use when users ask: 'Show my wallet', 'What's my wallet address?', 'My wallet'. "
                    "Do NOT use for checking balances - use check_wallet_balance instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "check_wallet_balance",
                "description": (
                    "Get complete wallet balance showing all tokens held with quantities and USD values. "
                    "Use when users ask: 'Check my balance', 'Show my tokens', 'What do I own?', "
                    "'My holdings', 'Wallet balance', 'What tokens do I have?', 'Portfolio'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "send_payment_tool",
                "description": (
                    "Send SOL, USDC, or USDT to another user by their Telegram username. "
                    "Creates a preview that user must confirm before execution. "
                    "Use when users say: 'send X SOL to @username', 'pay @user 10 USDC', 'transfer 5 USDT to @recipient'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "recipient_username": {
                            "type": "string",
                            "description": "Recipient's Telegram username (with or without @)"
                        },
                        "amount": {
                            "type": "string",
                            "description": "Amount to send as string (e.g., '0.5', '10')"
                        },
                        "token": {
                            "type": "string",
                            "description": "Token to send: 'SOL', 'USDC', or 'USDT'"
                        }
                    },
                    "required": ["recipient_username", "amount", "token"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "add_channel_monitoring",
                "description": "Add a Telegram channel to monitor for trading signals. Use when user says 'monitor @channel' or 'add @channel'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_username": {
                            "type": "string",
                            "description": "Channel username (with or without @)"
                        }
                    },
                    "required": ["channel_username"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "remove_channel_monitoring",
                "description": "Stop monitoring a channel for signals. Use when user says 'stop monitoring @channel' or 'remove @channel'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_username": {
                            "type": "string",
                            "description": "Channel username to remove"
                        }
                    },
                    "required": ["channel_username"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "list_monitored_channels",
                "description": (
                    "List all channels being monitored for signals. "
                    "Use when user asks: 'What channels am I monitoring?', 'Show my channels', 'List channels'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "get_signal_history",
                "description": (
                    "Get trading signals detected from monitored Telegram channels. "
                    "Supports filtering by date range and specific channel. "
                    "Use when user asks: 'Show my signals', 'What signals were detected?', "
                    "'Signals from @channel', 'Signals from last week'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of signals to retrieve. Default 10 if not specified."
                        },
                        "before_date": {
                            "type": "string",
                            "description": "ISO date string (YYYY-MM-DD). Get signals BEFORE this date. Optional."
                        },
                        "after_date": {
                            "type": "string",
                            "description": "ISO date string (YYYY-MM-DD). Get signals AFTER this date. Optional."
                        },
                        "channel": {
                            "type": "string",
                            "description": "Filter by specific channel username (without @). Optional."
                        }
                    },
                    "required": ["limit"],
                    "additionalProperties": False
                },
                "strict": False
            },
            {
                "type": "function",
                "name": "set_phone_number",
                "description": "Set or update phone number for receiving call alerts when signals are detected.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "phone_number": {
                            "type": "string",
                            "description": "Phone number with country code (e.g., +1234567890)"
                        }
                    },
                    "required": ["phone_number"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "get_phone_number",
                "description": "Get user's current phone number and verification status. Use when user asks 'What's my phone number?', 'Show my phone', 'Is my phone verified?'",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "toggle_calls",
                "description": "Enable or disable all phone call alerts globally.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "description": "True to enable calls, False to disable"
                        }
                    },
                    "required": ["enabled"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "toggle_channel_calls",
                "description": "Enable or disable call alerts for a specific channel.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_username": {
                            "type": "string",
                            "description": "Channel username"
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "True to enable, False to disable"
                        }
                    },
                    "required": ["channel_username", "enabled"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "configure_settings",
                "description": "Configure trading settings like slippage percentage for swaps.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "slippage_percent": {
                            "type": "number",
                            "description": "Slippage tolerance in percent (e.g., 2.5 for 2.5%)"
                        }
                    },
                    "required": ["slippage_percent"],
                    "additionalProperties": False
                },
                "strict": True
            },
            {
                "type": "function",
                "name": "get_statistics",
                "description": (
                    "Get comprehensive trading statistics and performance metrics. "
                    "Shows: total swaps (from signals vs manual), trading volume, response rates, "
                    "channels monitored, payments sent/received, member since date. "
                    "Supports filtering by date range. "
                    "Use when user asks: 'Show my stats', 'My performance', 'Trading statistics', "
                    "'Stats from last month', 'How many swaps have I done?'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "before_date": {
                            "type": "string",
                            "description": "ISO date string (YYYY-MM-DD). Get stats for period BEFORE this date. Optional."
                        },
                        "after_date": {
                            "type": "string",
                            "description": "ISO date string (YYYY-MM-DD). Get stats for period AFTER this date. Optional."
                        }
                    },
                    "required": [],
                    "additionalProperties": False
                },
                "strict": False
            }
        ]
        
    # ==================== MAIN AI CALL (RESPONSES API) ====================
    
    def call_ai(self, user_message: str, telegram_id: int, previous_response_id: str = None) -> Tuple[str, str, Dict]:
        """Call OpenAI Responses API with function calling and enhanced context"""
        try:
            # Try with previous_response_id first
            if previous_response_id:
                try:
                    response = self.client.responses.create(
                        model=OPENAI_MODEL,
                        previous_response_id=previous_response_id,
                        input=[{"role": "user", "content": user_message}],
                        tools=self.get_tools(),
                        store=True
                    )
                except Exception as e:
                    logger.warning(f"Failed to use previous_response_id: {e}")
                    logger.info("Starting fresh conversation")
                    response = self.client.responses.create(
                        model=OPENAI_MODEL,
                        instructions=self.system_instructions,
                        input=[{"role": "user", "content": user_message}],
                        tools=self.get_tools(),
                        store=True
                    )
            else:
                response = self.client.responses.create(
                    model=OPENAI_MODEL,
                    instructions=self.system_instructions,
                    input=[{"role": "user", "content": user_message}],
                    tools=self.get_tools(),
                    store=True
                )
            
            # Handle function calling
            if response.output and isinstance(response.output, list) and len(response.output) > 0:
                first_output = response.output[0]
                
                if hasattr(first_output, 'type') and first_output.type == "function_call":
                    function_name = first_output.name
                    function_args = json.loads(first_output.arguments)
                    call_id = first_output.call_id
                    
                    logger.info(f"AI calling: {function_name}")
                    logger.info(f"Function args: {function_args}")
                    
                    # Execute function
                    result = self._execute_tool(function_name, function_args, telegram_id)
                    logger.error(f"[DEBUG] Tool result: {result}")  
                    
                    if result is None:
                        logger.error(f"{function_name} returned None!")
                        result = {"success": False, "error": "Function returned no data"}
                    
                    logger.info(f"Function result success: {result.get('success', False)}")
                    
                    # Build enhanced instructions with context
                    enhanced_instructions = self._build_enhanced_instructions(
                        user_message,
                        function_name,
                        result
                    )
                    
                    # Build conversation history
                    conversation_history = [
                        {"role": "user", "content": user_message},
                        first_output,
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result)
                        }
                    ]
                    
                    try:
                        # Use enhanced instructions for second call
                        final_response = self.client.responses.create(
                            model=OPENAI_MODEL,
                            instructions=enhanced_instructions,
                            input=conversation_history,
                            tools=self.get_tools(),
                            store=True
                        )
                        
                        return final_response.output_text, final_response.id, result
                    except Exception as e:
                        logger.error(f"Error getting final response: {e}", exc_info=True)
                        return "I had trouble completing that request. Please try again.", None, result
                else:
                    return response.output_text, response.id, None
            
            return response.output_text, response.id, None
        
        except Exception as e:
            logger.error(f"OpenAI error: {e}", exc_info=True)
            return "I encountered an error processing your request. Please try again.", None, None
    
    # ==================== TOOL EXECUTION ====================
    
    def _execute_tool(self, function_name: str, function_args: Dict, telegram_id: int) -> Dict:
        """Execute the requested tool"""
        try:
            if function_name == "display_user_wallet":
                return self._display_wallet(telegram_id)
            
            elif function_name == "get_bot_info":
                return self._get_bot_info(function_args.get("query_type", "all"))
            
            elif function_name == "check_wallet_balance":
                return self._check_balance(telegram_id)
            
            elif function_name == "search_token_tool":
                return self._search_token(function_args.get("query"))
            
            elif function_name == "get_swap_preview_tool":
                return self._get_swap_preview(
                    telegram_id,
                    function_args.get("input_token"),
                    function_args.get("output_token"),
                    function_args.get("amount")
                )
            
            elif function_name == "send_payment_tool":
                return self._send_payment(
                    telegram_id,
                    function_args.get("recipient_username"),
                    function_args.get("amount"),
                    function_args.get("token")
                )
            
            elif function_name == "get_transfer_history":
                return self._get_transfer_history(
                    telegram_id,
                    limit=function_args.get("limit", 10),
                    before_date=function_args.get("before_date"),
                    after_date=function_args.get("after_date"),
                    username=function_args.get("username"),
                    direction=function_args.get("direction", "all")
                )
            
            elif function_name == "get_swap_history":
                return self._get_swap_history(
                    telegram_id,
                    limit=function_args.get("limit", 10),
                    before_date=function_args.get("before_date"),
                    after_date=function_args.get("after_date")
                )
            
            elif function_name == "add_channel_monitoring":
                return self._add_channel(telegram_id, function_args.get("channel_username"))
            
            elif function_name == "remove_channel_monitoring":
                return self._remove_channel(telegram_id, function_args.get("channel_username"))
            
            elif function_name == "list_monitored_channels":
                return self._list_channels(telegram_id)
            
            elif function_name == "get_signal_history":
                return self._get_signals(
                    telegram_id,
                    limit=function_args.get("limit", 10),
                    before_date=function_args.get("before_date"),
                    after_date=function_args.get("after_date"),
                    channel=function_args.get("channel")
                )
            
            elif function_name == "set_phone_number":
                return self._set_phone(telegram_id, function_args.get("phone_number"))
            
            elif function_name == "get_phone_number":
                return self._get_phone_number(telegram_id)
            
            elif function_name == "toggle_calls":
                return self._toggle_calls(telegram_id, function_args.get("enabled"))
            
            elif function_name == "toggle_channel_calls":
                return self._toggle_channel_calls(
                    telegram_id,
                    function_args.get("channel_username"),
                    function_args.get("enabled")
                )
            
            elif function_name == "configure_settings":
                return self._configure_settings(telegram_id, function_args)
            
            elif function_name == "get_statistics":
                return self._get_statistics(
                    telegram_id,
                    before_date=function_args.get("before_date"),
                    after_date=function_args.get("after_date")
                )
            
            else:
                return {"success": False, "error": "Unknown function"}
                
        except Exception as e:
            logger.error(f"Error executing {function_name}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    # ==================== PART 2 ORIGINAL TOOL IMPLEMENTATIONS ====================
    
    def _search_token(self, query: str) -> Dict:
        """Search for token information"""
        try:
            result = self._search_jupiter_token(query)
                        
            if not result["success"]:
                return {
                    "success": False,
                    "error": f"Could not find token '{query}'"
                }
            
            tokens = result.get("tokens", [])
            if not tokens:
                return {
                    "success": False,
                    "error": f"No tokens found for '{query}'"
                }
            
            # DEBUG: Log raw token data before formatting
            top_token = tokens[0]            
            formatted_token = self._format_token_info(top_token)
            
            if formatted_token:
                return {
                    "success": True,
                    "token_data": formatted_token,
                    "message": "Token found successfully"
                }
            else:
                return {
                    "success": False,
                    "error": "Error formatting token data"
                }
                
        except Exception as e:
            logger.error(f"Token search error: {e}")
            return {"success": False, "error": str(e)}
    
    def _get_swap_preview(self, telegram_id: int, input_token: str, output_token: str, amount: str) -> Dict:
        """Get swap preview with Jupiter quote - returns detailed preview for confirmation"""
        try:
            logger.info(f"[SWAP] Getting preview for {amount} {input_token} → {output_token}")
            
            # Check wallet
            user = self.db.get_user(telegram_id)
            if not user or not user.get("wallet_address"):
                logger.warning(f"[SWAP] No wallet found for user {telegram_id}")
                return {
                    "success": False, 
                    "error": "No wallet found. Please create or import a wallet first using /createwallet or /importwallet"
                }
            
            wallet_address = user.get("wallet_address")
            logger.info(f"[SWAP] User wallet: {wallet_address}")
            
            # Normalize SOL input
            if input_token.upper() == "SOL":
                input_token = "So11111111111111111111111111111111111111112"
            if output_token.upper() == "SOL":
                output_token = "So11111111111111111111111111111111111111112"
            
            # Validate addresses (32-44 chars, base58)
            if len(input_token) < 32 or len(output_token) < 32:
                return {
                    "success": False,
                    "error": "Invalid token address. Must be 32-44 character base58 string."
                }
            
            # Get token decimals
            input_decimals = 9 if input_token == "So11111111111111111111111111111111111111112" else 6
            amount_lamports = int(float(amount) * (10 ** input_decimals))
            
            logger.info(f"[SWAP] Fetching quote from Jupiter...")
            logger.info(f"[SWAP] Amount in smallest unit: {amount_lamports}")
            
            # Get quote from Jupiter using /order endpoint (NO taker = just quote)
            slippage_bps = int(user.get("slippage_percent", 5) * 100)
            
            order_data = self.jupiter.get_swap_order(
                input_mint=input_token,
                output_mint=output_token,
                amount=amount_lamports,
                slippage_bps=slippage_bps,
                taker_address=None  # ← No taker = preview only
            )
            
            if not order_data:
                logger.error(f"[SWAP] Failed to get order from Jupiter")
                return {
                    "success": False,
                    "error": "Failed to get swap quote. The token pair may not have liquidity or the amount is too high."
                }
            
            logger.info(f"[SWAP] Order received: {order_data}")
            
            # Extract order details
            output_amount_raw = order_data.get("outAmount", 0)
            output_decimals = 9 if output_token == "So11111111111111111111111111111111111111112" else 6
            output_amount = float(output_amount_raw) / (10 ** output_decimals)
            
            # Get price impact
            price_impact = float(order_data.get("priceImpactPct", 0))
            
            logger.info(f"[SWAP] Output amount: {output_amount}")
            logger.info(f"[SWAP] Price impact: {price_impact}%")
            
            # Return preview data
            return {
                "success": True,
                "action": "swap_preview",
                "input_token": input_token,
                "output_token": output_token,
                "amount": amount,
                "output_amount": output_amount,
                "price_impact": price_impact,
                "slippage_bps": slippage_bps,
                "quote": order_data,  # Store full order data
                "needs_confirmation": True
            }
        
        except Exception as e:
            logger.error(f"[SWAP] Exception in _get_swap_preview: {e}", exc_info=True)
            return {
                "success": False, 
                "error": f"Error getting swap quote: {str(e)}"
            }
    
    def _check_balance(self, telegram_id: int) -> Dict:
        """Check wallet balance"""
        try:
            user = self.db.get_user(telegram_id)
            
            if not user or not user.get("wallet_address"):
                return {"success": False, "message": "No wallet found"}
            
            wallet_address = user.get("wallet_address")
            balance_data = self._get_wallet_balance(wallet_address)
            
            if not balance_data["success"]:
                return balance_data
            
            # Return RAW DATA - let AI format it
            return {
                "success": True,
                "wallet_address": wallet_address,
                "tokens": balance_data.get("tokens", []),
                "total_value_usd": balance_data.get("total_value_usd", 0),
                "token_count": balance_data.get("token_count", 0)
            }
        except Exception as e:
            logger.error(f"Error checking balance: {e}")
            return {"success": False, "error": str(e)}
    
    def _display_wallet(self, telegram_id: int) -> Dict:
        """Display wallet information"""
        try:
            user = self.db.get_user(telegram_id)
            if not user or not user.get("wallet_address"):
                return {"success": False, "message": "No wallet found"}
            
            # Convert datetime to string for JSON serialization
            created_at = user.get("wallet_created_at")
            if created_at:
                created_at = str(created_at)
            else:
                created_at = "Unknown"
            
            return {
                "success": True,
                "wallet_address": user.get("wallet_address"),
                "wallet_type": user.get("wallet_type", "Unknown"),
                "created_at": created_at
            }
        except Exception as e:
            logger.error(f"Error displaying wallet: {e}")
            return {"success": False, "error": str(e)}
        
    def _get_bot_info(self, query_type: str = "all") -> Dict:
        """Return information about Cortexa bot"""
        
        bot_info = {
            "name": "Cortexa",
            "tagline": "The most advanced AI-powered DeFi assistant on Solana",
            
            "about": (
                "Cortexa eliminates crypto complexity through natural conversation. "
                "No commands, no complicated interfaces - just talk like you would to a friend. "
                "Everything is fully powered by AI that understands context and remembers conversations."
            ),
            
            "core_capabilities": [
                {
                    "name": "Instant Payments by Username",
                    "description": "Send SOL or any SPL token to ANY Telegram user just by their @username. No wallet addresses needed. I handle wallet creation, transaction execution, and notifications automatically.",
                    "examples": ["Send 5 SOL to @alice", "Pay @bob 100 USDC", "Transfer 0.5 SOL to @friend"]
                },
                {
                    "name": "AI-Powered Instant Swaps",
                    "description": "Describe what you want in plain English and I execute it. Best routes via Jupiter, optimal prices, one-click confirmation.",
                    "security_rule": "For security, I only accept full contract addresses (32-44 characters) for swaps. Exception: 'SOL' for native Solana. Search the token first to get its contract address - this protects you from scam tokens.",
                    "examples": ["Search BONK (get contract first)", "Swap 1 SOL for [contract_address]"]
                },
                {
                    "name": "KOL Signal Tracking with Phone Alerts",
                    "description": "Add alpha channels, I monitor 24/7. When a buy signal drops, I call your phone in under 15 seconds. Press 1 for details, Press 2 to buy instantly. From signal to execution in 15 seconds.",
                    "setup": "1. 'Monitor @channel' → 2. 'Set my phone to +1234567890' → 3. Verify the call → 4. 'Enable calls for @channel'",
                    "examples": ["Monitor @solana_gems", "Set my phone to +1234567890", "Enable calls for @channel"]
                }
            ],
            
            "other_features": [
                "Wallet Management - Create/import wallets, check balances with portfolio insights",
                "Token Search - Get price, market cap, volume, liquidity, holders for any token",
                "Transaction History - View swaps and transfers with date/username filters",
                "Statistics - Track swaps, volume, signal response rates",
                "Group Support - Execute swaps and payments in groups (balances are DM-only)",
                "Phone Management - Set and verify phone for call alerts"
            ],
            
            "how_to_use": {
                "general": "Just talk naturally. No commands needed. I understand context.",
                "payments": "Say 'Send 5 SOL to @username' → Preview → Confirm → Done",
                "swaps": "Search token first → Get contract address → 'Swap 1 SOL for [address]' → Confirm",
                "kol_alerts": "Monitor channel → Set phone → Verify → Enable calls → Get instant alerts",
                "examples": ["What's my balance?", "Search BONK", "Send 1 SOL to @alice", "Show my swap history", "My channels", "What's my phone?"]
            },
            
            "important_rules": {
                "swap_security": "Contract addresses required for swaps (except SOL). Search token first to get verified address.",
                "group_privacy": "In groups: swaps and payments only. Balances and history are DM-only.",
                "phone_verification": "Phone must be verified before receiving call alerts."
            },
            
            "personality": "I'm confident and conversational. I handle complexity so you don't have to. From signal to execution in 15 seconds - that's the Cortexa advantage. I'm your edge in the market."
        }
        
        if query_type == "name":
            return {
                "success": True,
                "name": bot_info["name"],
                "tagline": bot_info["tagline"],
                "description": "I turn complex crypto operations into simple conversations."
            }
        
        elif query_type == "about":
            return {
                "success": True,
                "name": bot_info["name"],
                "tagline": bot_info["tagline"],
                "about": bot_info["about"],
                "personality": bot_info["personality"]
            }
        
        elif query_type == "capabilities":
            return {
                "success": True,
                "name": bot_info["name"],
                "core_capabilities": bot_info["core_capabilities"],
                "other_features": bot_info["other_features"],
                "important_rules": bot_info["important_rules"]
            }
        
        elif query_type == "how_to_use":
            return {
                "success": True,
                "name": bot_info["name"],
                "how_to_use": bot_info["how_to_use"],
                "important_rules": bot_info["important_rules"]
            }
        
        else:  # "all"
            return {
                "success": True,
                **bot_info
            }
        
    def _get_transfer_history(self, telegram_id: int, limit: int = 10,
                          before_date: str = None, after_date: str = None,
                          username: str = None, direction: str = "all") -> Dict:
        """Get payment/transfer history with optional filters"""
        try:
            user = self.db.get_user(telegram_id)
            if not user:
                return {"success": False, "error": "User not found"}
            
            transactions = user.get("transactions", [])
            transfers = [
                tx for tx in transactions 
                if tx.get('type') in ['outgoing_payment', 'incoming_payment']
            ]
            
            if direction == "sent":
                transfers = [tx for tx in transfers if tx.get('type') == 'outgoing_payment']
            elif direction == "received":
                transfers = [tx for tx in transfers if tx.get('type') == 'incoming_payment']
            
            if username:
                username_clean = username.lstrip('@').lower()
                filtered = []
                for tx in transfers:
                    recipient = tx.get('recipient_username', '').lower()
                    sender = tx.get('sender_username', '').lower()
                    if recipient == username_clean or sender == username_clean:
                        filtered.append(tx)
                transfers = filtered
            
            before_dt = _parse_date(before_date)
            after_dt = _parse_date(after_date)
            
            if before_dt or after_dt:
                filtered_transfers = []
                for tx in transfers:
                    tx_time = _to_naive_datetime(tx.get('timestamp'))
                    if tx_time is None:
                        continue
                    
                    if before_dt and tx_time >= before_dt:
                        continue
                    if after_dt and tx_time <= after_dt:
                        continue
                    filtered_transfers.append(tx)
                transfers = filtered_transfers
            
            if not transfers:
                filter_parts = []
                if username:
                    filter_parts.append(f"with @{username}")
                if direction == "sent":
                    filter_parts.append("that you sent")
                elif direction == "received":
                    filter_parts.append("that you received")
                if before_date:
                    filter_parts.append(f"before {before_date}")
                if after_date:
                    filter_parts.append(f"after {after_date}")
                
                filter_text = " ".join(filter_parts) if filter_parts else ""
                
                return {
                    "success": True,
                    "transfers": [],
                    "count": 0,
                    "filters_applied": {
                        "username": username,
                        "direction": direction,
                        "before_date": before_date,
                        "after_date": after_date
                    },
                    "message": f"No transfers found{' ' + filter_text if filter_text else ''}."
                }
            
            transfers_sorted = sorted(transfers, key=lambda x: str(x.get('timestamp', '')), reverse=True)
            recent_transfers = transfers_sorted[:limit]
            
            formatted_transfers = []
            for tx in recent_transfers:
                signature = tx.get('signature', '')
                tx_type = tx.get('type', '')
                is_outgoing = (tx_type == 'outgoing_payment')
                
                if is_outgoing:
                    tx_direction = "sent"
                    other_user = tx.get('recipient_username', 'Unknown')
                else:
                    tx_direction = "received"
                    other_user = tx.get('sender_username', 'Unknown')
                
                formatted_transfer = {
                    "datetime": _format_datetime_utc(tx.get('timestamp')),
                    "direction": tx_direction,
                    "amount": f"{tx.get('amount', 0)} {tx.get('token', 'Unknown')}",
                    "other_user": f"@{other_user}",
                    "solscan_link": f"https://solscan.io/tx/{signature}" if signature else None
                }
                formatted_transfers.append(formatted_transfer)
            
            all_transfers = [tx for tx in user.get("transactions", []) 
                            if tx.get('type') in ['outgoing_payment', 'incoming_payment']]
            total_sent = sum(1 for tx in all_transfers if tx.get('type') == 'outgoing_payment')
            total_received = sum(1 for tx in all_transfers if tx.get('type') == 'incoming_payment')
            
            return {
                "success": True,
                "transfers": formatted_transfers,
                "count": len(formatted_transfers),
                "total_transfers": len(transfers),
                "summary": {
                    "total_sent": total_sent,
                    "total_received": total_received
                },
                "filters_applied": {
                    "username": username,
                    "direction": direction,
                    "before_date": before_date,
                    "after_date": after_date,
                    "limit": limit
                }
            }
        
        except Exception as e:
            logger.error(f"Get transfer history error: {e}")
            return {"success": False, "error": str(e)}
        
    def _get_swap_history(self, telegram_id: int, limit: int = 10, 
                      before_date: str = None, after_date: str = None) -> Dict:
        """Get swap/trade history with optional date filters"""
        try:
            user = self.db.get_user(telegram_id)
            if not user:
                return {"success": False, "error": "User not found"}
            
            transactions = user.get("transactions", [])
            swaps = [tx for tx in transactions if tx.get('type') == 'swap']
            
            before_dt = _parse_date(before_date)
            after_dt = _parse_date(after_date)
            
            if before_dt or after_dt:
                filtered_swaps = []
                for tx in swaps:
                    tx_time = _to_naive_datetime(tx.get('timestamp'))
                    if tx_time is None:
                        continue
                    
                    if before_dt and tx_time >= before_dt:
                        continue
                    if after_dt and tx_time <= after_dt:
                        continue
                    filtered_swaps.append(tx)
                swaps = filtered_swaps
            
            if not swaps:
                filters_applied = []
                if before_date:
                    filters_applied.append(f"before {before_date}")
                if after_date:
                    filters_applied.append(f"after {after_date}")
                filter_text = " ".join(filters_applied) if filters_applied else ""
                
                return {
                    "success": True,
                    "swaps": [],
                    "count": 0,
                    "filters_applied": {
                        "before_date": before_date,
                        "after_date": after_date
                    },
                    "message": f"No swaps found{' ' + filter_text if filter_text else ''}."
                }
            
            swaps_sorted = sorted(swaps, key=lambda x: str(x.get('timestamp', '')), reverse=True)
            recent_swaps = swaps_sorted[:limit]
            
            formatted_swaps = []
            for tx in recent_swaps:
                signature = tx.get('signature', '')
                input_token = tx.get('input_token', 'Unknown')
                output_token = tx.get('output_token', 'Unknown')
                input_symbol = tx.get('input_symbol', '')
                output_symbol = tx.get('output_symbol', '')
                
                if input_symbol:
                    input_display = input_symbol
                elif input_token == "So11111111111111111111111111111111111111112":
                    input_display = "SOL"
                elif len(input_token) > 10:
                    input_display = f"{input_token[:6]}...{input_token[-4:]}"
                else:
                    input_display = input_token
                
                if output_symbol:
                    output_display = output_symbol
                elif output_token == "So11111111111111111111111111111111111111112":
                    output_display = "SOL"
                elif len(output_token) > 10:
                    output_display = f"{output_token[:6]}...{output_token[-4:]}"
                else:
                    output_display = output_token
                
                formatted_swap = {
                    "datetime": _format_datetime_utc(tx.get('timestamp')),
                    "sold": f"{tx.get('input_amount', 0)} {input_display}",
                    "bought": f"{tx.get('output_amount', 0)} {output_display}",
                    "source": tx.get('source', 'user'),
                    "solscan_link": f"https://solscan.io/tx/{signature}" if signature else None
                }
                formatted_swaps.append(formatted_swap)
            
            return {
                "success": True,
                "swaps": formatted_swaps,
                "count": len(formatted_swaps),
                "total_swaps": len(swaps),
                "filters_applied": {
                    "before_date": before_date,
                    "after_date": after_date,
                    "limit": limit
                }
            }
        
        except Exception as e:
            logger.error(f"Get swap history error: {e}")
            return {"success": False, "error": str(e)}

    
    def _send_payment(self, telegram_id: int, recipient_username: str, amount: str, token: str) -> Dict:
        """Send payment to another user - returns preview for confirmation"""
        try:
            logger.info(f"[PAYMENT] Starting payment validation: {amount} {token} to @{recipient_username}")
            
            # Step 1: Normalize inputs
            token = token.upper()
            recipient_username = recipient_username.lstrip('@').lower()
            
            # Step 2: Validate amount
            try:
                amount_float = float(amount)
                if amount_float <= 0:
                    return {"success": False, "error": "Amount must be greater than 0"}
            except ValueError:
                return {"success": False, "error": "Invalid amount format"}
            
            # Step 3: Validate token
            if token not in self.SUPPORTED_PAYMENT_TOKENS:
                return {
                    "success": False,
                    "error": f"Unsupported token. Only SOL, USDC, and USDT are supported."
                }
            
            token_config = self.SUPPORTED_PAYMENT_TOKENS[token]
            
            # Step 4: Verify sender has wallet
            sender = self.db.get_user(telegram_id)
            if not sender or not sender.get("wallet_address"):
                return {
                    "success": False,
                    "error": "No wallet found. Please create or import a wallet first using /createwallet or /importwallet"
                }
            
            sender_wallet = sender.get("wallet_address")
            sender_username = sender.get("username", "unknown")
            
            # Step 5: Look up recipient and determine status
            recipient = self.db.get_user_by_username(recipient_username)
            
            if not recipient:
                # CASE A: Brand new user
                recipient_status = "new_user"
                recipient_telegram_id = None
                recipient_wallet = None
                needs_wallet_creation = True
                needs_rent_exemption = True
                logger.info(f"[PAYMENT] Recipient is NEW USER")
                
            elif recipient.get("telegram_id") and recipient.get("wallet_address"):
                # CASE B: Active user with wallet
                recipient_status = "active"
                recipient_telegram_id = recipient["telegram_id"]
                recipient_wallet = recipient["wallet_address"]
                needs_wallet_creation = False
                needs_rent_exemption = False
                logger.info(f"[PAYMENT] Recipient is ACTIVE USER with wallet")
                
            elif recipient.get("telegram_id") and not recipient.get("wallet_address"):
                # CASE C: User without wallet
                recipient_status = "user_no_wallet"
                recipient_telegram_id = recipient["telegram_id"]
                recipient_wallet = None
                needs_wallet_creation = True
                needs_rent_exemption = True
                logger.info(f"[PAYMENT] Recipient is USER WITHOUT WALLET")
                
            elif not recipient.get("telegram_id") and recipient.get("pending_wallet_address"):
                # CASE D: Pending wallet (previous payment created wallet)
                recipient_status = "pending_wallet"
                recipient_telegram_id = None
                recipient_wallet = recipient["pending_wallet_address"]
                needs_wallet_creation = False
                needs_rent_exemption = False
                logger.info(f"[PAYMENT] Recipient has PENDING WALLET")
                
            else:
                return {"success": False, "error": "Unable to determine recipient status"}
            
            # Step 6: Calculate network fees
            network_fee = self._estimate_network_fee(needs_rent_exemption, token)
            logger.info(f"[PAYMENT] Network fee: {network_fee} SOL")
            
            # Step 7: Check minimum transfer for new accounts
            if needs_rent_exemption and token == "SOL":
                if amount_float < MINIMUM_SOL_TRANSFER_NEW_USER:
                    return {
                        "success": False,
                        "error": f"Minimum transfer to new users is {MINIMUM_SOL_TRANSFER_NEW_USER} SOL (includes rent-exemption fee)"
                    }

            # Step 7.5: ADDITIONAL CHECK - If "active" user, verify their blockchain balance
            if recipient_status == "active" and recipient_wallet and token == "SOL":
                logger.info(f"[PAYMENT] Checking recipient's actual blockchain balance...")
                
                from services.alchemy_transfer import alchemy_transfer
                recipient_balance = alchemy_transfer.get_sol_balance(recipient_wallet)
                
                # If recipient has zero or insufficient balance for rent-exemption
                if recipient_balance < 0.00089088:
                    logger.info(f"[PAYMENT] Recipient has {recipient_balance} SOL (below rent threshold)")
                    
                    # Check if user is trying to send below minimum
                    if amount_float < MINIMUM_SOL_TRANSFER_NEW_USER:
                        return {
                            "success": False,
                            "error": f"Recipient's wallet is empty or has insufficient balance. Minimum transfer is {MINIMUM_SOL_TRANSFER_NEW_USER} SOL (covers Solana's rent-exemption requirement of ~0.00089 SOL)"
                        }
                    
                    # Update needs_rent_exemption for accurate fee calculation
                    needs_rent_exemption = True
                    logger.info(f"[PAYMENT] Updated needs_rent_exemption to True for active user with empty wallet")
            
            # Step 8: Check sender balance
            balance_check = self._check_payment_balance(
                telegram_id,
                amount_float,
                token,
                network_fee
            )
            
            if not balance_check["success"]:
                return balance_check
            
            # Step 9: Determine recipient status text for display
            if recipient_status == "new_user":
                status_text = "new user (wallet will be created)"
            elif recipient_status == "user_no_wallet":
                status_text = "user (wallet will be created)"
            elif recipient_status == "pending_wallet":
                status_text = "user (has pending wallet)"
            else:
                status_text = "active user"
            
            # Step 10: Calculate total cost
            if token == "SOL":
                total_cost = amount_float + network_fee
            else:
                total_cost = amount_float  # SPL tokens, gas is separate
            
            # Step 11: Return payment preview
            return {
                "success": True,
                "payment_data": {
                    "recipient_username": recipient_username,
                    "recipient_wallet": recipient_wallet,
                    "recipient_status": recipient_status,
                    "recipient_telegram_id": recipient_telegram_id,
                    "amount": str(amount_float),
                    "token": token,
                    "token_mint": token_config["mint"],
                    "token_decimals": token_config["decimals"],
                    "network_fee": network_fee,
                    "needs_wallet_creation": needs_wallet_creation,
                    "sender_username": sender_username,
                    "sender_wallet": sender_wallet
                },
                "display": {
                    "recipient": f"@{recipient_username}",
                    "amount": f"{amount_float} {token}",
                    "fee": f"{network_fee} SOL",
                    "total": f"{total_cost} {token}" if token == "SOL" else f"{amount_float} {token} + {network_fee} SOL",
                    "recipient_status_text": status_text
                }
            }
        
        except Exception as e:
            logger.error(f"[PAYMENT] Error in _send_payment: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        
    def _estimate_network_fee(self, needs_rent_exemption: bool, token: str) -> float:
        """Estimate network fee for transaction"""
        try:
            base_fee = BASE_TRANSACTION_FEE
            
            if needs_rent_exemption:
                # New account needs rent-exemption
                total_fee = base_fee + RENT_EXEMPTION_FEE
            else:
                total_fee = base_fee
            
            # For SPL tokens, add ATA creation fee if needed
            if token in ["USDC", "USDT"] and needs_rent_exemption:
                total_fee += ATA_CREATION_FEE
            
            return total_fee
            
        except Exception as e:
            logger.error(f"Error estimating fee: {e}")
            return BASE_TRANSACTION_FEE + RENT_EXEMPTION_FEE  # Safe default

    def _check_payment_balance(self, telegram_id: int, amount: float, 
                            token: str, network_fee: float) -> Dict:
        """Check if sender has sufficient balance"""
        try:
            user = self.db.get_user(telegram_id)
            if not user or not user.get("wallet_address"):
                return {"success": False, "error": "Wallet not found"}
            
            wallet_address = user["wallet_address"]
            
            # Get balances via Alchemy
            balances = self._get_wallet_balance_alchemy(wallet_address)
            
            if token == "SOL":
                # Need amount + fee in SOL
                required = amount + network_fee
                available = balances.get("SOL", 0)
                
                if available < required:
                    return {
                        "success": False,
                        "error": f"Insufficient balance. You have {available:.6f} SOL, need {required:.6f} SOL (includes {network_fee:.6f} SOL network fee)"
                    }
            
            else:  # USDC or USDT
                # Need token amount + SOL for gas
                token_available = balances.get(token, 0)
                sol_available = balances.get("SOL", 0)
                
                if token_available < amount:
                    return {
                        "success": False,
                        "error": f"Insufficient {token}. You have {token_available:.2f} {token}, need {amount:.2f} {token}"
                    }
                
                if sol_available < network_fee:
                    return {
                        "success": False,
                        "error": f"Insufficient SOL for gas. You have {sol_available:.6f} SOL, need {network_fee:.6f} SOL for transaction fee"
                    }
            
            return {"success": True}
            
        except Exception as e:
            logger.error(f"Balance check error: {e}")
            return {"success": False, "error": "Failed to check balance"}

    def _get_wallet_balance_alchemy(self, wallet_address: str) -> Dict:
        """Get wallet balance in simple format for payment validation"""
        try:
            # Use the main balance function
            balance_data = self._get_wallet_balance(wallet_address)
            
            if not balance_data.get("success"):
                return {"SOL": 0, "USDC": 0, "USDT": 0}
            
            # Convert to simple dict format
            balances = {"SOL": 0, "USDC": 0, "USDT": 0}
            
            for token in balance_data.get("tokens", []):
                symbol = token.get("symbol", "").upper()
                if symbol in balances:
                    balances[symbol] = token.get("balance", 0)
            
            logger.info(f"[BALANCE] {wallet_address[:8]}... -> SOL: {balances['SOL']}, USDC: {balances['USDC']}, USDT: {balances['USDT']}")
            
            return balances
            
        except Exception as e:
            logger.error(f"Balance check error: {e}")
            return {"SOL": 0, "USDC": 0, "USDT": 0}
    
    # ==================== PART 1 NEW TOOL IMPLEMENTATIONS ====================
    
    def _add_channel(self, telegram_id: int, channel_username: str) -> Dict:
        """Add channel to monitoring - DIRECT (NO CELERY)"""
        try:
            channel_username = channel_username.lstrip('@')
            
            logger.info(f"[ADD_CHANNEL] User {telegram_id} wants to monitor @{channel_username}")
            
            # Return success - actual addition happens in bot_handlers via async
            return {
                "success": True,
                "message": f"Adding @{channel_username} to monitoring...",
                "channel": channel_username,
                "action": "add_channel"  # Flag for bot_handlers to process
            }
            
        except Exception as e:
            logger.error(f"[ADD_CHANNEL] Error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }


    def _remove_channel(self, telegram_id: int, channel_username: str) -> Dict:
        """Remove channel from monitoring - DIRECT (NO CELERY)"""
        try:
            channel_username = channel_username.lstrip('@')
            
            # Check if user is monitoring this channel
            channels = self.db.get_active_channels(telegram_id)
            is_monitoring = any(ch.get("channel_username") == channel_username for ch in channels)
            
            if not is_monitoring:
                return {
                    "success": False,
                    "error": f"You're not monitoring @{channel_username}"
                }
            
            logger.info(f"[REMOVE_CHANNEL] User {telegram_id} wants to remove @{channel_username}")
            
            return {
                "success": True,
                "message": f"Removing @{channel_username}...",
                "channel": channel_username,
                "action": "remove_channel"  # Flag for bot_handlers
            }
            
        except Exception as e:
            logger.error(f"[REMOVE_CHANNEL] Error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _list_channels(self, telegram_id: int) -> Dict:
        """List monitored channels"""
        try:
            channels = self.db.get_active_channels(telegram_id)
            
            # Convert datetime to string for JSON serialization
            for ch in channels:
                if ch.get('added_at'):
                    ch['added_at'] = str(ch['added_at'])
            
            return {
                "success": True,
                "channels": channels,
                "count": len(channels)
            }
        
        except Exception as e:
            logger.error(f"List channels error: {e}")
            return {"success": False, "error": str(e)}
    
    def _get_signals(self, telegram_id: int, limit: int = 10,
                 before_date: str = None, after_date: str = None,
                 channel: str = None) -> Dict:
        """Get signal history with optional filters"""
        try:
            user = self.db.get_user(telegram_id)
            if not user:
                return {"success": False, "error": "User not found"}
            
            signals = user.get("signal_history", [])
            
            if channel:
                channel_clean = channel.lstrip('@').lower()
                signals = [s for s in signals 
                        if s.get('channel_username', '').lower() == channel_clean]
            
            before_dt = _parse_date(before_date)
            after_dt = _parse_date(after_date)
            
            if before_dt or after_dt:
                filtered_signals = []
                for signal in signals:
                    signal_time = _to_naive_datetime(signal.get('detected_at'))
                    if signal_time is None:
                        continue
                    
                    if before_dt and signal_time >= before_dt:
                        continue
                    if after_dt and signal_time <= after_dt:
                        continue
                    filtered_signals.append(signal)
                signals = filtered_signals
            
            if not signals:
                filter_parts = []
                if channel:
                    filter_parts.append(f"from @{channel}")
                if before_date:
                    filter_parts.append(f"before {before_date}")
                if after_date:
                    filter_parts.append(f"after {after_date}")
                filter_text = " ".join(filter_parts) if filter_parts else ""
                
                return {
                    "success": True,
                    "signals": [],
                    "count": 0,
                    "filters_applied": {
                        "channel": channel,
                        "before_date": before_date,
                        "after_date": after_date
                    },
                    "message": f"No signals found{' ' + filter_text if filter_text else ''}."
                }
            
            signals_sorted = sorted(signals, key=lambda x: str(x.get('detected_at', '')), reverse=True)
            recent_signals = signals_sorted[:limit]
            
            formatted_signals = []
            for signal in recent_signals:
                formatted_signal = {
                    "token_symbol": signal.get('token_symbol', 'Unknown'),
                    "token_address": signal.get('token_address', ''),
                    "channel": f"@{signal.get('channel_username', 'Unknown')}",
                    "datetime": _format_datetime_utc(signal.get('detected_at')),
                    "confidence": round(signal.get('confidence', 0) * 100),
                    "executed": signal.get('swap_executed', False)
                }
                formatted_signals.append(formatted_signal)
            
            return {
                "success": True,
                "signals": formatted_signals,
                "count": len(formatted_signals),
                "total_signals": len(signals),
                "filters_applied": {
                    "channel": channel,
                    "before_date": before_date,
                    "after_date": after_date,
                    "limit": limit
                }
            }
        
        except Exception as e:
            logger.error(f"Get signals error: {e}")
            return {"success": False, "error": str(e)}
    
    def _set_phone(self, telegram_id: int, phone_number: str) -> Dict:
        """Set phone number - initiates verification process"""
        try:
            # Step 1: Normalize phone number
            phone_number = phone_number.strip()
            if not phone_number.startswith('+'):
                phone_number = '+' + phone_number
            
            # Step 2: Validate phone number format
            validation = self._validate_phone_number(phone_number)
            if not validation["valid"]:
                return {
                    "success": False,
                    "error": validation["error"]
                }
            
            # Step 3: Save as pending (not verified)
            self.db.set_phone_pending(telegram_id, phone_number)
            
            # Step 4: Return action flag for bot_handlers to start verification
            return {
                "success": True,
                "action": "start_phone_verification",
                "phone_number": phone_number,
                "message": "Phone number saved. Starting verification process..."
            }
        
        except Exception as e:
            logger.error(f"Set phone error: {e}")
            return {"success": False, "error": str(e)}
        
    def _get_phone_number(self, telegram_id: int) -> Dict:
        """Get user's phone number and verification status"""
        try:
            user = self.db.get_user(telegram_id)
            if not user:
                return {"success": False, "error": "User not found"}
            
            phone_number = user.get("phone_number")
            phone_verified = user.get("phone_verified", False)
            calls_enabled = user.get("calls_enabled", False)
            
            if not phone_number:
                return {
                    "success": True,
                    "phone_number": None,
                    "message": "No phone number set. Set one by saying 'Set my phone to +1234567890'"
                }
            
            return {
                "success": True,
                "phone_number": phone_number,
                "verified": phone_verified,
                "calls_enabled": calls_enabled,
                "message": f"Phone: {phone_number} | Verified: {'Yes' if phone_verified else 'No'} | Calls: {'Enabled' if calls_enabled else 'Disabled'}"
            }
        
        except Exception as e:
            logger.error(f"Get phone number error: {e}")
            return {"success": False, "error": str(e)}


    def _validate_phone_number(self, phone_number: str) -> Dict:
        """Validate phone number format and country code"""
        import re
        
        # Must start with +
        if not phone_number.startswith('+'):
            return {
                "valid": False,
                "error": "Phone number must start with + and country code (e.g., +919876543210)"
            }
        
        # Remove + for digit validation
        digits_only = phone_number[1:]
        
        # Check if all digits
        if not digits_only.isdigit():
            return {
                "valid": False,
                "error": "Phone number must contain only digits after country code"
            }
        
        # Check length (minimum 10, maximum 15 including country code)
        if len(digits_only) < 10 or len(digits_only) > 15:
            return {
                "valid": False,
                "error": "Phone number must be 10-15 digits including country code"
            }
        
        # Valid country codes (common ones)
        valid_country_codes = [
            '1',    # USA, Canada
            '7',    # Russia
            '20',   # Egypt
            '27',   # South Africa
            '30',   # Greece
            '31',   # Netherlands
            '32',   # Belgium
            '33',   # France
            '34',   # Spain
            '36',   # Hungary
            '39',   # Italy
            '40',   # Romania
            '41',   # Switzerland
            '43',   # Austria
            '44',   # UK
            '45',   # Denmark
            '46',   # Sweden
            '47',   # Norway
            '48',   # Poland
            '49',   # Germany
            '51',   # Peru
            '52',   # Mexico
            '53',   # Cuba
            '54',   # Argentina
            '55',   # Brazil
            '56',   # Chile
            '57',   # Colombia
            '58',   # Venezuela
            '60',   # Malaysia
            '61',   # Australia
            '62',   # Indonesia
            '63',   # Philippines
            '64',   # New Zealand
            '65',   # Singapore
            '66',   # Thailand
            '81',   # Japan
            '82',   # South Korea
            '84',   # Vietnam
            '86',   # China
            '90',   # Turkey
            '91',   # India
            '92',   # Pakistan
            '93',   # Afghanistan
            '94',   # Sri Lanka
            '95',   # Myanmar
            '98',   # Iran
            '212',  # Morocco
            '213',  # Algeria
            '216',  # Tunisia
            '218',  # Libya
            '220',  # Gambia
            '221',  # Senegal
            '234',  # Nigeria
            '254',  # Kenya
            '255',  # Tanzania
            '256',  # Uganda
            '260',  # Zambia
            '261',  # Madagascar
            '263',  # Zimbabwe
            '264',  # Namibia
            '265',  # Malawi
            '266',  # Lesotho
            '267',  # Botswana
            '268',  # Eswatini
            '269',  # Comoros
            '351',  # Portugal
            '352',  # Luxembourg
            '353',  # Ireland
            '354',  # Iceland
            '355',  # Albania
            '356',  # Malta
            '357',  # Cyprus
            '358',  # Finland
            '359',  # Bulgaria
            '370',  # Lithuania
            '371',  # Latvia
            '372',  # Estonia
            '373',  # Moldova
            '374',  # Armenia
            '375',  # Belarus
            '376',  # Andorra
            '377',  # Monaco
            '378',  # San Marino
            '380',  # Ukraine
            '381',  # Serbia
            '382',  # Montenegro
            '383',  # Kosovo
            '385',  # Croatia
            '386',  # Slovenia
            '387',  # Bosnia
            '389',  # North Macedonia
            '420',  # Czech Republic
            '421',  # Slovakia
            '423',  # Liechtenstein
            '852',  # Hong Kong
            '853',  # Macau
            '855',  # Cambodia
            '856',  # Laos
            '880',  # Bangladesh
            '886',  # Taiwan
            '960',  # Maldives
            '961',  # Lebanon
            '962',  # Jordan
            '963',  # Syria
            '964',  # Iraq
            '965',  # Kuwait
            '966',  # Saudi Arabia
            '967',  # Yemen
            '968',  # Oman
            '970',  # Palestine
            '971',  # UAE
            '972',  # Israel
            '973',  # Bahrain
            '974',  # Qatar
            '975',  # Bhutan
            '976',  # Mongolia
            '977',  # Nepal
            '992',  # Tajikistan
            '993',  # Turkmenistan
            '994',  # Azerbaijan
            '995',  # Georgia
            '996',  # Kyrgyzstan
            '998',  # Uzbekistan
        ]
        
        # Check if starts with valid country code
        is_valid_country = False
        for code in valid_country_codes:
            if digits_only.startswith(code):
                is_valid_country = True
                break
        
        if not is_valid_country:
            return {
                "valid": False,
                "error": "Invalid country code. Please use a valid international format (e.g., +1 for USA, +91 for India)"
            }
        
        return {"valid": True}
    
    def _toggle_calls(self, telegram_id: int, enabled: bool) -> Dict:
        """Toggle call alerts globally (affects all channels)"""
        try:
            success = self.db.toggle_calls(telegram_id, enabled)
            
            if success:
                status = "enabled" if enabled else "disabled"
                return {
                    "success": True,
                    "message": f"Phone call alerts {status} for all channels"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to update call settings"
                }
        
        except Exception as e:
            logger.error(f"Toggle calls error: {e}")
            return {"success": False, "error": str(e)}
    
    def _toggle_channel_calls(self, telegram_id: int, channel_username: str, enabled: bool) -> Dict:
        """Toggle calls for specific channel"""
        try:
            channel_username = channel_username.lstrip('@').lower()
            
            success = self.db.toggle_channel_calls(telegram_id, channel_username, enabled)
            
            if success:
                status = "enabled" if enabled else "disabled"
                return {
                    "success": True,
                    "message": f"Calls {status} for @{channel_username}"
                }
            else:
                channels = self.db.get_active_channels(telegram_id)
                channel_names = [ch.get("channel_username", "").lower() for ch in channels]
                
                if channel_username not in channel_names:
                    return {
                        "success": False,
                        "error": f"Channel @{channel_username} not found in your monitored channels"
                    }
                
                return {
                    "success": False,
                    "error": "Failed to update channel settings"
                }
        
        except Exception as e:
            logger.error(f"Toggle channel calls error: {e}")
            return {"success": False, "error": str(e)}
    
    def _configure_settings(self, telegram_id: int, settings: Dict) -> Dict:
        """Configure trading settings"""
        try:
            success = self.db.update_trading_settings(telegram_id, settings)
            
            if success:
                updated = []
                if "slippage_percent" in settings:
                    updated.append(f"Slippage: {settings['slippage_percent']}%")
                if "max_trade_amount_sol" in settings:
                    updated.append(f"Max trade: {settings['max_trade_amount_sol']} SOL")
                
                return {
                    "success": True,
                    "message": f"Settings updated: {', '.join(updated)}"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to update settings"
                }
        
        except Exception as e:
            logger.error(f"Configure settings error: {e}")
            return {"success": False, "error": str(e)}
    
    def _get_statistics(self, telegram_id: int, 
                    before_date: str = None, after_date: str = None) -> Dict:
        """Get user statistics with optional date range filter"""
        try:
            user = self.db.get_user(telegram_id)
            if not user:
                return {"success": False, "error": "User not found"}
            
            before_dt = _parse_date(before_date)
            after_dt = _parse_date(after_date)
            
            transactions = user.get("transactions", [])
            signals = user.get("signal_history", [])
            
            if before_dt or after_dt:
                filtered_tx = []
                for tx in transactions:
                    tx_time = _to_naive_datetime(tx.get('timestamp'))
                    if tx_time is None:
                        continue
                    
                    if before_dt and tx_time >= before_dt:
                        continue
                    if after_dt and tx_time <= after_dt:
                        continue
                    filtered_tx.append(tx)
                transactions = filtered_tx
                
                filtered_signals = []
                for signal in signals:
                    signal_time = _to_naive_datetime(signal.get('detected_at'))
                    if signal_time is None:
                        continue
                    
                    if before_dt and signal_time >= before_dt:
                        continue
                    if after_dt and signal_time <= after_dt:
                        continue
                    filtered_signals.append(signal)
                signals = filtered_signals
            
            swaps = [tx for tx in transactions if tx.get('type') == 'swap']
            signal_swaps = [tx for tx in swaps if tx.get('source') == 'signal']
            user_swaps = [tx for tx in swaps if tx.get('source') != 'signal']
            
            total_volume = sum(float(tx.get('input_amount', 0)) for tx in swaps 
                            if tx.get('input_token') == 'So11111111111111111111111111111111111111112' 
                            or tx.get('input_symbol', '').upper() == 'SOL')
            
            payments_sent = sum(1 for tx in transactions if tx.get('type') == 'outgoing_payment')
            payments_received = sum(1 for tx in transactions if tx.get('type') == 'incoming_payment')
            
            active_channels = len(self.db.get_active_channels(telegram_id))
            
            signals_executed = sum(1 for s in signals if s.get('swap_executed'))
            calls_made = sum(1 for s in signals if s.get('call_made'))
            response_rate = (signals_executed / len(signals) * 100) if signals else 0
            
            member_since = user.get('created_at')
            if member_since:
                member_since = _format_datetime_utc(member_since)
            else:
                member_since = "Unknown"
            
            statistics = {
                "total_swaps": len(swaps),
                "swaps_from_signals": len(signal_swaps),
                "swaps_manual": len(user_swaps),
                "total_volume_sol": round(total_volume, 2),
                "active_channels": active_channels,
                "total_signals": len(signals),
                "signals_executed": signals_executed,
                "calls_made": calls_made,
                "response_rate": round(response_rate, 1),
                "payments_sent": payments_sent,
                "payments_received": payments_received,
                "member_since": member_since
            }
            
            return {
                "success": True,
                "statistics": statistics,
                "filters_applied": {
                    "before_date": before_date,
                    "after_date": after_date
                }
            }
        
        except Exception as e:
            logger.error(f"Get statistics error: {e}")
            return {"success": False, "error": str(e)}
    
    # ==================== JUPITER API METHODS ====================
    
    def _search_jupiter_token(self, query: str) -> Dict:
        """Search for Solana tokens via Jupiter API"""
        try:
            tokens = self.jupiter.search_tokens(query)
            
            if not tokens:
                return {
                    "success": False,
                    "message": f"No tokens found for '{query}'"
                }
            
            return {
                "success": True,
                "tokens": tokens[:5] if len(tokens) > 5 else tokens,
                "total_found": len(tokens)
            }
            
        except Exception as e:
            logger.error(f"Error searching token: {e}")
            return {"success": False, "error": str(e)}
    
    def _format_token_info(self, token_data: Dict) -> Dict:
        """Format token information for clean display"""
        try:
            # Extract stats24h data
            stats24h = token_data.get("stats24h", {})
            stats7d = token_data.get("stats7d", {})
            
            # Calculate total 24h volume (buy + sell)
            volume_24h = None
            if stats24h:
                buy_vol = stats24h.get("buyVolume", 0)
                sell_vol = stats24h.get("sellVolume", 0)
                volume_24h = buy_vol + sell_vol if (buy_vol and sell_vol) else None
            
            # Get price changes
            price_change_24h = stats24h.get("priceChange") if stats24h else None
            price_change_7d = stats7d.get("priceChange") if stats7d else None
            
            formatted = {
                "name": token_data.get("name"),
                "symbol": token_data.get("symbol"),
                "mint_address": token_data.get("id"),  # FIXED: was "mint", should be "id"
                "price_usd": token_data.get("usdPrice"),
                "market_cap": token_data.get("mcap"),
                "market_cap_formatted": self._format_large_number(token_data.get("mcap")),
                "liquidity": token_data.get("liquidity"),
                "liquidity_formatted": self._format_large_number(token_data.get("liquidity")),
                "volume_24h": volume_24h,
                "volume_24h_formatted": self._format_large_number(volume_24h),
                "price_change_24h": price_change_24h,
                "price_change_7d": price_change_7d,
                "holder_count": token_data.get("holderCount"),
                "holder_count_formatted": self._format_large_number(token_data.get("holderCount")),
                "verified": token_data.get("isVerified", False),  # FIXED: was "verified", should be "isVerified"
                "tags": token_data.get("tags", []),
            }
            
            # Remove None values
            formatted = {k: v for k, v in formatted.items() if v is not None}
                        
            return formatted
                
        except Exception as e:
            logger.error(f"Error formatting token: {e}", exc_info=True)
            return None
        
    def _format_large_number(self, num: float) -> str:
        """Format large numbers with K/M/B suffix"""
        if num is None:
            return "N/A"
        
        try:
            num = float(num)
            if num >= 1_000_000_000:
                return f"{num / 1_000_000_000:.2f}B"
            elif num >= 1_000_000:
                return f"{num / 1_000_000:.2f}M"
            elif num >= 1_000:
                return f"{num / 1_000:.2f}K"
            else:
                return f"{num:.2f}"
        except:
            return "N/A"
    
    # ==================== ALCHEMY BALANCE CHECKING ====================
    
    def _get_wallet_balance(self, wallet_address: str) -> Dict:
        """Fetch Solana wallet balance using Alchemy Data API"""
        try:
            if not self.alchemy_api_key or self.alchemy_api_key == "your_alchemy_api_key_here":
                logger.error("Alchemy API key not configured")
                return {
                    "success": False,
                    "error": "Alchemy API key not configured"
                }
            
            url = f"https://api.g.alchemy.com/data/v1/{self.alchemy_api_key}/assets/tokens/by-address"
            
            payload = {
                "addresses": [{
                    "address": wallet_address,
                    "networks": ["solana-mainnet"]
                }]
            }
            
            response = requests.post(url, headers={}, json=payload, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                tokens = data.get("data", {}).get("tokens", [])
                
                tokens_with_balance = []
                total_portfolio_value = 0
                
                for token in tokens:
                    try:
                        token_mint = token.get("tokenAddress")
                        is_native_sol = (token_mint is None or token_mint == "")
                        
                        if is_native_sol:
                            token_mint = "So11111111111111111111111111111111111111112"
                        
                        token_balance = token.get("scaledTokenBalance")
                        
                        if token_balance is None:
                            hex_balance = token.get("tokenBalance", "0x0")
                            
                            if isinstance(hex_balance, str) and hex_balance.startswith("0x"):
                                raw_balance_int = int(hex_balance, 16)
                                
                                if is_native_sol:
                                    decimals = 9
                                else:
                                    decimals = token.get("tokenMetadata", {}).get("decimals")
                                    if decimals is None:
                                        continue
                                
                                if decimals and decimals > 0:
                                    token_balance = raw_balance_int / (10 ** decimals)
                                else:
                                    token_balance = float(raw_balance_int)
                            else:
                                token_balance = float(hex_balance) if hex_balance else 0
                        else:
                            token_balance = float(token_balance)
                        
                        if token_balance and float(token_balance) > 0:
                            metadata = token.get("tokenMetadata", {})
                            
                            if is_native_sol:
                                token_name = "Solana"
                                token_symbol = "SOL"
                            else:
                                token_name = metadata.get("name") or "Unknown Token"
                                token_symbol = metadata.get("symbol") or "Unknown"
                            
                            if token_balance < 1:
                                rounded_balance = round(float(token_balance), 6)
                            else:
                                rounded_balance = round(float(token_balance), 2)
                            
                            token_prices = token.get("tokenPrices", [])
                            usd_value = None
                            if token_prices and len(token_prices) > 0:
                                usd_price_str = token_prices[0].get("value")
                                if usd_price_str and usd_price_str != "0":
                                    try:
                                        usd_price = float(usd_price_str)
                                        if usd_price > 0:
                                            usd_value = round(rounded_balance * usd_price, 2)
                                    except (ValueError, TypeError):
                                        pass
                            
                            tokens_with_balance.append({
                                "name": token_name,
                                "symbol": token_symbol,
                                "mint_address": token_mint,
                                "balance": rounded_balance,
                                "usd_value": usd_value
                            })
                            
                            if usd_value:
                                total_portfolio_value += usd_value
                            
                    except Exception as e:
                        logger.warning(f"Error processing token: {e}")
                        continue
                
                return {
                    "success": True,
                    "wallet_address": wallet_address,
                    "tokens": tokens_with_balance,
                    "token_count": len(tokens_with_balance),
                    "total_value_usd": round(total_portfolio_value, 2)
                }
            else:
                return {
                    "success": False,
                    "error": f"API error: {response.status_code}"
                }
                
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return {"success": False, "error": str(e)}
        
    # ==================== CONTEXT BUILDING ====================
    
    def _build_enhanced_instructions(self, user_message: str, function_name: str, function_result: dict) -> str:
        """Build context-aware instructions for the second API call"""
        
        user_message_lower = user_message.lower()
        
        clean_query = user_message
        if "[CONTEXT:" in user_message:
            parts = user_message.split("\n\n")
            if len(parts) > 1:
                clean_query = parts[-1]
        
        context_guide = get_context_guide(user_message, function_name)
        
        filters_info = ""
        if function_result.get("filters_applied"):
            filters = function_result["filters_applied"]
            filter_parts = []
            if filters.get("username"):
                filter_parts.append(f"username: @{filters['username']}")
            if filters.get("direction") and filters["direction"] != "all":
                filter_parts.append(f"direction: {filters['direction']}")
            if filters.get("before_date"):
                filter_parts.append(f"before: {filters['before_date']}")
            if filters.get("after_date"):
                filter_parts.append(f"after: {filters['after_date']}")
            if filters.get("channel"):
                filter_parts.append(f"channel: @{filters['channel']}")
            
            if filter_parts:
                filters_info = f"\nFilters applied: {', '.join(filter_parts)}"
        
        enhanced_instructions = f"""{self.system_instructions}

    ════════════════════════════════════════════════════════
    CONTEXT FOR THIS RESPONSE
    ════════════════════════════════════════════════════════

    User query: "{clean_query}"
    Function: {function_name}
    {context_guide}
    {filters_info}

    CRITICAL FORMATTING RULES:
    1. Start with an INSIGHTFUL intro (2-3 sentences explaining the data, not just announcing it)
    2. Use INLINE format for data: <b>Label:</b> Value (same line, no break between label and value)
    3. NO extra line breaks between data fields - keep compact
    4. Only use <code> tags for contract addresses and tx hashes
    5. Only use <a href> for Solscan links
    6. NO emojis, NO dashes, NO bullet points, NO numbered lists
    7. End with ONE relevant follow-up question
    8. Dates as: 15 Jan 2025, 14:30 UTC

    EXAMPLE INLINE FORMAT:
    <b>Price:</b> $0.00000957
    <b>Market Cap:</b> $789.29M
    <b>24h Change:</b> +2.52%

    NOT like this (wrong):
    <b>Price</b>
    $0.00000957

    ════════════════════════════════════════════════════════
    """
        
        return enhanced_instructions