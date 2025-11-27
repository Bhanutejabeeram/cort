import logging
import re
from typing import Dict, Optional
from datetime import datetime

from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Gather

from config import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER, CALL_TIMEOUT_SECONDS, WEBHOOK_URL
)

logger = logging.getLogger(__name__)


class TwilioHandler:
    """Handles Twilio phone calls with natural voice and retry logic"""
    
    def __init__(self, database):
        """Initialize Twilio handler"""
        self.db = database
        self.last_call_sid = None  # Store last call SID for Redis key
        
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            self.client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            self.enabled = True
            
            # UPGRADED: Use Amazon Polly GENERATIVE voice (most realistic)
            # Same voice as webhook for consistency
            self.voice = 'Polly.Matthew-Generative'
            
            logger.info("✅ Twilio handler initialized with Polly.Matthew-Generative voice")
        else:
            self.client = None
            self.enabled = False
            logger.warning("Twilio not configured - calls disabled")
    
    def _format_channel_name_for_speech(self, channel_name: str) -> str:
        """
        Format channel name for natural speech
        - Replace underscores with spaces
        - Remove special symbols (except letters, numbers, spaces)
        - Clean up multiple spaces
        """
        if not channel_name:
            return "unknown channel"
        
        # Remove @ if present at the start
        channel_name = channel_name.lstrip('@')
        
        # Replace underscores with spaces
        channel_name = channel_name.replace('_', ' ')
        
        # Remove special characters (keep only letters, numbers, spaces)
        channel_name = re.sub(r'[^a-zA-Z0-9\s]', '', channel_name)
        
        # Clean up multiple spaces
        channel_name = re.sub(r'\s+', ' ', channel_name).strip()
        
        # If empty after cleaning, return default
        if not channel_name:
            return "unknown channel"
        
        return channel_name
    
    def _format_market_cap_for_speech(self, market_cap: float) -> str:
        """
        Format market cap for natural speech
        Examples:
        - 1,500,000,000 -> "1.5 billion dollars"
        - 850,000,000 -> "850 million dollars"
        - 45,000,000 -> "45 million dollars"
        - 2,500,000 -> "2.5 million dollars"
        - 500,000 -> "500 thousand dollars"
        """
        if not market_cap or market_cap <= 0:
            return None
        
        try:
            market_cap = float(market_cap)
            
            if market_cap >= 1_000_000_000:
                # Billions
                value = market_cap / 1_000_000_000
                if value == int(value):
                    return f"{int(value)} billion dollars"
                else:
                    return f"{value:.1f} billion dollars"
                    
            elif market_cap >= 1_000_000:
                # Millions
                value = market_cap / 1_000_000
                if value == int(value):
                    return f"{int(value)} million dollars"
                else:
                    return f"{value:.1f} million dollars"
                    
            elif market_cap >= 1_000:
                # Thousands
                value = market_cap / 1_000
                if value == int(value):
                    return f"{int(value)} thousand dollars"
                else:
                    return f"{value:.1f} thousand dollars"
            else:
                return f"{int(market_cap)} dollars"
                
        except (ValueError, TypeError):
            return None
    
    def _build_call_script(self, token_symbol: str, token_name: str, 
                      channel_name: str = None, price: float = None,
                      market_cap: float = None, username: str = None) -> str:
        
        # Format channel name for speech
        formatted_channel = self._format_channel_name_for_speech(channel_name) if channel_name else "A channel"
        
        # Format username (capitalize first letter for natural speech)
        if username:
            formatted_username = username.replace('_', ' ').strip().title()
        else:
            formatted_username = None
        
        # Format market cap
        formatted_mcap = self._format_market_cap_for_speech(market_cap) if market_cap else None
        
        # Build conversational script with proper punctuation
        if formatted_username:
            greeting = f"Hey, {formatted_username}!"
        else:
            greeting = "Hey!"
        
        if formatted_mcap:
            script = f"{greeting} {formatted_channel} just called {token_name}, trading at {formatted_mcap}. Willing to buy?"
        else:
            script = f"{greeting} {formatted_channel} just called {token_name}. Willing to buy?"
        
        return script
    
    async def make_signal_call(self, phone_number: str, token_symbol: str, token_name: str, 
                           channel_name: str = None, price: float = None, 
                           market_cap: float = None, username: str = None) -> bool:
        """
        Make phone call for trading signal with natural voice and retry logic
        
        FEATURES:
        - Amazon Polly GENERATIVE voice (most human-like)
        - Personalized greeting with username
        - Natural punctuation for speech pauses
        - 3 retry attempts with 20s timeout
        - Status callback tracking
        """
        if not self.enabled:
            return False
        
        try:
            # BUILD NATURAL CALL SCRIPT
            call_script = self._build_call_script(
                token_symbol, token_name, channel_name, price, market_cap, username
            )
            
            logger.info(f"[TWILIO] Making call to {phone_number}")
            logger.info(f"[TWILIO] Script: {call_script}")
            
            # CREATE TwiML USING VoiceResponse
            response = VoiceResponse()
            
            # Opening message with GENERATIVE voice
            response.say(call_script, voice=self.voice)
            
            # ADD PAUSE (natural breath before options)
            response.pause(length=1)
            
            # ADD GATHER WITH RETRY LOGIC
            self._add_gather_with_retry(
                response, 
                webhook_url=WEBHOOK_URL, 
                attempt=1,
                prompt="Press 1 to buy, 0 to skip."
            )
            
            # MAKE CALL WITH STATUS CALLBACK
            call = self.client.calls.create(
                to=phone_number,
                from_=TWILIO_PHONE_NUMBER,
                twiml=str(response),
                timeout=CALL_TIMEOUT_SECONDS,
                status_callback=f"{WEBHOOK_URL}/call-status",
                status_callback_event=['completed', 'failed', 'no-answer', 'busy']
            )
            
            # CRITICAL: Store the call SID for webhook lookup
            self.last_call_sid = call.sid
            
            logger.info(f"[TWILIO] ✅ Call initiated: {call.sid}")
            return True
        
        except Exception as e:
            logger.error(f"[TWILIO] Make call error: {e}", exc_info=True)
            return False
    
    async def make_verification_call(self, phone_number: str, username: str = None) -> bool:
        """
        Make phone call for phone number verification
        
        Script: "Hey, [Username]! This is Cortexa calling to verify your phone number.
                Please enter the 4 digit code you received in the bot, followed by hash."
        """
        if not self.enabled:
            return False
        
        try:
            # Format username
            if username:
                formatted_username = username.replace('_', ' ').strip().title()
                greeting = f"Hey, {formatted_username}!"
            else:
                greeting = "Hey!"
            
            # Build verification call script
            call_script = (
                f"{greeting} This is Cortexa calling to verify your phone number. "
                f"Please enter the 4 digit code you received in the bot, followed by hash."
            )
            
            logger.info(f"[TWILIO] Making verification call to {phone_number}")
            logger.info(f"[TWILIO] Script: {call_script}")
            
            # CREATE TwiML USING VoiceResponse
            response = VoiceResponse()
            
            # Opening message
            response.say(call_script, voice=self.voice)
            
            # Pause for natural flow
            response.pause(length=1)
            
            # Gather 4-digit code
            gather = Gather(
                num_digits=4,
                action=f"{WEBHOOK_URL}/verify-code",
                method='POST',
                timeout=30,
                finish_on_key='#'
            )
            gather.say("Enter your code now.", voice=self.voice)
            response.append(gather)
            
            # If no input received
            response.say(
                "No code received. Please check your Telegram for the code and try again. Goodbye!",
                voice=self.voice
            )
            response.hangup()
            
            # MAKE CALL
            call = self.client.calls.create(
                to=phone_number,
                from_=TWILIO_PHONE_NUMBER,
                twiml=str(response),
                timeout=CALL_TIMEOUT_SECONDS,
                status_callback=f"{WEBHOOK_URL}/verify-call-status",
                status_callback_event=['completed', 'failed', 'no-answer', 'busy']
            )
            
            # Store call SID
            self.last_call_sid = call.sid
            
            logger.info(f"[TWILIO] ✅ Verification call initiated: {call.sid}")
            return True
        
        except Exception as e:
            logger.error(f"[TWILIO] Verification call error: {e}", exc_info=True)
            return False
    
    def _add_gather_with_retry(self, response: VoiceResponse, webhook_url: str, 
                               attempt: int, prompt: str):
        """
        Add Gather with retry logic (up to 3 attempts)
        
        FEATURES:
        - 20 second timeout (plenty of time to respond)
        - Retries up to 3 times with helpful messages
        - Graceful fallback to Telegram if no response
        - Uses same GENERATIVE voice throughout
        """
        gather = Gather(
            num_digits=1,
            action=f"{webhook_url}/gather?attempt={attempt}",
            method='POST',
            timeout=20  # 20 seconds - more time to respond
        )
        
        # Say the prompt with GENERATIVE voice
        gather.say(prompt, voice=self.voice)
        response.append(gather)
        
        # RETRY LOGIC: If no input and haven't reached max attempts
        if attempt < 3:
            # Helpful retry message
            response.say("I didn't receive your response.", voice=self.voice)
            response.pause(length=1)
            
            # Redirect to retry
            response.redirect(f"{webhook_url}/retry-gather?attempt={attempt + 1}")
        else:
            # MAX ATTEMPTS REACHED: Graceful fallback
            response.say(
                "No response received after three attempts. "
                "I'll send you the details on Telegram. Goodbye!",
                voice=self.voice
            )
            response.hangup()
    
    def generate_call_script(self, token_symbol: str, token_name: str) -> str:
        """Legacy method - kept for compatibility"""
        return self._build_call_script(token_symbol, token_name)