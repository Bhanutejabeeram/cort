"""
Cortex Unified Bot - Configuration Management
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4")

# MongoDB Configuration
MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "cortex_unified")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "users")

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

REDIS_URL="rediss://default:AWkFAAIncDI0MjE2ZDMzOGZlYzc0ZWViOWI0MTkyZGFiYmRmZjA3YXAyMjY4ODU@discrete-spaniel-26885.upstash.io:6379"

# Webhook Configuration
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://localhost:5000")

# Jupiter API Configuration
JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "")
JUPITER_API_URL = os.getenv("JUPITER_API_URL", "https://api.jup.ag/ultra/v1")
# Use paid endpoint if API key provided, otherwise use lite
JUPITER_BASE_URL = "https://api.jup.ag/ultra/v1" if JUPITER_API_KEY else "https://lite-api.jup.ag/ultra/v1"

# Alchemy API Configuration
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "")

# Solana Configuration
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

# Encryption Configuration
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

# Application Settings
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "5"))
CALL_TIMEOUT_SECONDS = int(os.getenv("CALL_TIMEOUT_SECONDS", "30"))
DEFAULT_SLIPPAGE_BPS = int(os.getenv("DEFAULT_SLIPPAGE_BPS", "500"))
DEFAULT_SLIPPAGE_PERCENT = 5  # For user display

# Logging Configuration
LOG_FILE = os.getenv("LOG_FILE", "logs/cortex.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# User Configuration (for single-user mode from Part 1)
USER_PHONE_NUMBER = os.getenv("USER_PHONE_NUMBER")
# Phone Verification Settings
VERIFICATION_CODE_EXPIRY = 600  # 10 minutes in seconds
VERIFICATION_CODE_LENGTH = 4

# Payment Tokens Configuration
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

# Network Fee Constants (Solana)
BASE_TRANSACTION_FEE = 0.000005  # 5,000 lamports - signature fee
RENT_EXEMPTION_FEE = 0.00089088  # 890,880 lamports - new account rent
ATA_CREATION_FEE = 0.00204  # ~2 million lamports - Associated Token Account

# Minimum Transfer Amounts
MINIMUM_SOL_TRANSFER_NEW_USER = 0.001  # Covers rent-exemption fee

# SPL Token Program IDs
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"

# Solana RPC via Alchemy
ALCHEMY_SOLANA_RPC = f"https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"