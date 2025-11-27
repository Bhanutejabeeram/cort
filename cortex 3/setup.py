#!/usr/bin/env python3
"""
Cortex Unified Bot - Setup and Testing Script
Helps initialize the environment and test connections
"""

import os
import sys
import time
from cryptography.fernet import Fernet


def print_header(text):
    """Print formatted header"""
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def print_step(step, text):
    """Print step with formatting"""
    print(f"[{step}] {text}")


def check_env_file():
    """Check if .env file exists"""
    if os.path.exists('.env'):
        print("✅ .env file found")
        return True
    else:
        print("❌ .env file not found")
        print("\nPlease create a .env file based on .env.template")
        return False


def generate_encryption_key():
    """Generate a new encryption key"""
    key = Fernet.generate_key().decode()
    print(f"\n🔐 Generated Encryption Key:\n{key}\n")
    print("Add this to your .env file:")
    print(f"ENCRYPTION_KEY={key}")
    return key


def test_imports():
    """Test if all required packages are installed"""
    print_header("Testing Package Imports")
    
    packages = {
        "telegram": "python-telegram-bot",
        "telethon": "telethon",
        "openai": "openai",
        "pymongo": "pymongo",
        "twilio": "twilio",
        "solders": "solders",
        "cryptography": "cryptography",
        "flask": "flask",
        "requests": "requests",
        "loguru": "loguru"
    }
    
    missing = []
    
    for module, package in packages.items():
        try:
            __import__(module)
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package}")
            missing.append(package)
    
    if missing:
        print(f"\n❌ Missing packages: {', '.join(missing)}")
        print("\nInstall with:")
        print(f"pip install {' '.join(missing)}")
        return False
    
    print("\n✅ All packages installed")
    return True


def test_mongodb():
    """Test MongoDB connection"""
    print_header("Testing MongoDB Connection")
    
    try:
        from pymongo import MongoClient
        from dotenv import load_dotenv
        
        load_dotenv()
        uri = os.getenv("MONGODB_URI")
        database = os.getenv("MONGODB_DATABASE")
        
        if not uri:
            print("❌ MONGODB_URI not set in .env")
            return False
        
        print("Connecting to MongoDB...")
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.server_info()
        
        # Test database
        db = client[database]
        
        print("✅ MongoDB connection successful")
        print(f"   Database: {database}")
        return True
    
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")
        return False


def test_telegram():
    """Test Telegram bot token"""
    print_header("Testing Telegram Bot")
    
    try:
        from dotenv import load_dotenv
        import requests
        
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        
        if not token:
            print("❌ TELEGRAM_BOT_TOKEN not set in .env")
            return False
        
        # Test token with getMe API call
        url = f"https://api.telegram.org/bot{token}/getMe"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                bot_info = data.get("result", {})
                print("✅ Telegram bot token valid")
                print(f"   Bot: @{bot_info.get('username', 'unknown')}")
                print(f"   Name: {bot_info.get('first_name', 'unknown')}")
                return True
        
        print("❌ Invalid Telegram bot token")
        return False
    
    except Exception as e:
        print(f"❌ Telegram test failed: {e}")
        return False


def test_openai():
    """Test OpenAI API"""
    print_header("Testing OpenAI API")
    
    try:
        from dotenv import load_dotenv
        from openai import OpenAI
        
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        
        if not api_key:
            print("❌ OPENAI_API_KEY not set in .env")
            return False
        
        print("Testing API connection...")
        client = OpenAI(api_key=api_key)
        
        # Simple test
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Say 'test'"}],
            max_tokens=10
        )
        
        print("✅ OpenAI API connection successful")
        return True
    
    except Exception as e:
        print(f"❌ OpenAI API test failed: {e}")
        return False


def test_jupiter():
    """Test Jupiter API"""
    print_header("Testing Jupiter API")
    
    try:
        import requests
        
        # Test public endpoint
        url = "https://lite-api.jup.ag/ultra/v1/search"
        params = {"query": "SOL"}
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if data and len(data) > 0:
            print("✅ Jupiter API connection successful")
            return True
        
        print("❌ Jupiter API returned no data")
        return False
    
    except Exception as e:
        print(f"❌ Jupiter API test failed: {e}")
        return False


def create_directories():
    """Create required directories"""
    print_header("Creating Directories")
    
    dirs = ["logs", "core", "services", "monitoring", "prompts"]
    
    for dir_name in dirs:
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)
            print(f"✅ Created {dir_name}/")
        else:
            print(f"✓ {dir_name}/ exists")
    
    return True


def main():
    """Main setup function"""
    print("="*60)
    print("  CORTEX UNIFIED BOT - SETUP & TESTING")
    print("="*60)
    
    # Parse arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--generate-key":
            generate_encryption_key()
            return
        elif sys.argv[1] == "--help":
            print("\nUsage:")
            print("  python setup.py          Run setup tests")
            print("  python setup.py --generate-key  Generate encryption key")
            return
    
    # Run tests
    tests = {
        "Environment File": check_env_file,
        "Directories": create_directories,
        "Package Imports": test_imports,
        "MongoDB": test_mongodb,
        "Telegram Bot": test_telegram,
        "OpenAI API": test_openai,
        "Jupiter API": test_jupiter
    }
    
    results = {}
    
    for name, test_func in tests.items():
        print_step(len(results) + 1, f"Testing {name}...")
        time.sleep(0.5)
        
        try:
            results[name] = test_func()
        except Exception as e:
            print(f"❌ Error during {name} test: {e}")
            results[name] = False
        
        print()
    
    # Summary
    print_header("Setup Summary")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, status in results.items():
        icon = "✅" if status else "❌"
        print(f"{icon} {name}")
    
    print(f"\n{passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All systems ready!")
        print("\nYou can now run the bot with:")
        print("  python main.py")
    else:
        print("\n⚠️ Please fix the failed tests before running the bot")
        
        if not results.get("Environment File"):
            print("\n1. Copy .env.template to .env")
            print("2. Fill in your API keys and credentials")
            print("3. Generate encryption key: python setup.py --generate-key")
    
    print()


if __name__ == "__main__":
    main()