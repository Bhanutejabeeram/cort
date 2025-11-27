"""
Cortex Bot - Twilio Webhook Handler (UPGRADED v2)
Handles phone call responses with MOST REALISTIC voice and retry logic
UPGRADED: Using Amazon Polly GENERATIVE voice (most human-like available)
"""

import logging
import json
import redis
from flask import Flask, request
from dotenv import load_dotenv
import os

from twilio.twiml.voice_response import VoiceResponse, Gather

load_dotenv()

from config import REDIS_URL

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize Redis client for call data lookup
redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    ssl_cert_reqs=None  # For Upstash
)

# ✅ UPGRADE: Amazon Polly GENERATIVE voice (MOST REALISTIC)
# This is the most human-like, emotionally engaged voice available on Twilio
# Options: Polly.Joanna-Generative (female) or Polly.Matthew-Generative (male)
VOICE = 'Polly.Matthew-Generative'


def get_active_call(twilio_call_sid: str):
    """Get active call data from Redis"""
    try:
        redis_key = f"active_call:{twilio_call_sid}"
        call_data_json = redis_client.get(redis_key)
        
        if call_data_json:
            call_data = json.loads(call_data_json)
            logger.info(f"[WEBHOOK] Found call data for {twilio_call_sid}")
            return call_data
        else:
            logger.warning(f"[WEBHOOK] No call data found in Redis for {twilio_call_sid}")
            return None
    except Exception as e:
        logger.error(f"[WEBHOOK] Error getting call data from Redis: {e}")
        return None


def remove_active_call(twilio_call_sid: str):
    """Remove call from Redis"""
    try:
        redis_key = f"active_call:{twilio_call_sid}"
        redis_client.delete(redis_key)
        logger.info(f"[WEBHOOK] Removed call data for {twilio_call_sid}")
    except Exception as e:
        logger.error(f"[WEBHOOK] Error removing call data: {e}")


# ==================== RETRY GATHER ENDPOINT ====================

@app.route('/retry-gather', methods=['POST'])
def retry_gather():
    """
    Handle retry for initial gather input (buy/skip)
    UPGRADED: Uses GENERATIVE voice (most realistic)
    """
    try:
        attempt = int(request.args.get('attempt', '1'))
        call_sid = request.form.get('CallSid')
        
        logger.info(f"[WEBHOOK] Retrying gather, attempt {attempt} for call {call_sid}")
        
        response = VoiceResponse()
        
        if attempt <= 3:
            # Re-ask the question with GENERATIVE voice
            gather = Gather(
                num_digits=1,
                action=f'/gather?attempt={attempt}',
                method='POST',
                timeout=20
            )
            
            # ✅ NATURAL RETRY PROMPT with most realistic voice
            gather.say(
                "Let me ask again. Press 1 to buy this token, or press 0 to skip.",
                voice=VOICE
            )
            response.append(gather)
            
            # If still no response and more attempts left
            if attempt < 3:
                response.say("I didn't receive your response.", voice=VOICE)
                response.pause(length=1)
                response.redirect(f"/retry-gather?attempt={attempt + 1}")
            else:
                # ✅ MAX ATTEMPTS: Graceful ending
                response.say(
                    "No response after three attempts. "
                    "Check Telegram for details. Goodbye.",
                    voice=VOICE
                )
                response.hangup()
                remove_active_call(call_sid)
        else:
            response.say("Maximum attempts reached. Goodbye.", voice=VOICE)
            response.hangup()
            remove_active_call(call_sid)
        
        return str(response)
        
    except Exception as e:
        logger.error(f"[WEBHOOK] Retry gather error: {e}")
        response = VoiceResponse()
        response.say("An error occurred. Goodbye.", voice=VOICE)
        response.hangup()
        return str(response)


# ==================== MAIN GATHER ENDPOINT ====================

@app.route('/gather', methods=['POST'])
def handle_gather():
    """
    Handle initial user response (1 for buy, 0 for skip)
    UPGRADED: GENERATIVE voice + better error handling + retry logic
    """
    try:
        digit = request.values.get('Digits')
        call_sid = request.values.get('CallSid')
        attempt = int(request.args.get('attempt', '1'))
        
        logger.info(f"[WEBHOOK] Received digit: {digit} for call {call_sid} (attempt {attempt})")
        
        response = VoiceResponse()
        
        if digit == '1':
            # ✅ USER WANTS TO BUY - Ask for amount with natural voice
            response.say("Great. Now let's specify the amount.", voice=VOICE)
            response.pause(length=1)
            
            # Gather amount with retry logic
            _add_amount_gather_with_retry(response, attempt=1)
            
        elif digit == '0':
            # ✅ USER SKIPPED - Acknowledge naturally
            response.say(
                "Okay, signal skipped. "
                "Check Telegram for the next opportunity. Goodbye.",
                voice=VOICE
            )
            response.hangup()
            remove_active_call(call_sid)
            
        else:
            # ✅ INVALID INPUT - Retry if attempts left
            logger.warning(f"[WEBHOOK] Invalid input: {digit}")
            
            if attempt < 3:
                response.say("Invalid input. Let me ask again.", voice=VOICE)
                response.pause(length=1)
                response.redirect(f'/retry-gather?attempt={attempt + 1}')
            else:
                response.say("Invalid input. Call ending. Goodbye.", voice=VOICE)
                response.hangup()
                remove_active_call(call_sid)
        
        return str(response)
        
    except Exception as e:
        logger.error(f"[WEBHOOK] Gather error: {e}", exc_info=True)
        
        response = VoiceResponse()
        response.say("An error occurred. Goodbye.", voice=VOICE)
        response.hangup()
        return str(response)


def _add_amount_gather_with_retry(response: VoiceResponse, attempt: int):
    """Add amount gather with retry logic using GENERATIVE voice"""
    gather = Gather(
        finish_on_key='#',
        action=f'/amount?attempt={attempt}',
        method='POST',
        timeout=20
    )
    
    # ✅ CLEAR INSTRUCTIONS WITH EXAMPLE - sounds natural with Generative voice
    gather.say(
        "Enter the amount in SOL to buy. "
        "For example, 1 star 5 for one point five SOL. "
        "Press hash when done.",
        voice=VOICE
    )
    response.append(gather)
    
    # Retry logic
    if attempt < 3:
        response.say("No amount received.", voice=VOICE)
        response.redirect(f'/retry-amount?attempt={attempt + 1}')
    else:
        response.say(
            "No amount received after three attempts. Goodbye.",
            voice=VOICE
        )
        response.hangup()


# ==================== AMOUNT HANDLERS ====================

@app.route('/retry-amount', methods=['POST'])
def retry_amount():
    """Handle retry for amount input with GENERATIVE voice"""
    try:
        attempt = int(request.args.get('attempt', '1'))
        call_sid = request.form.get('CallSid')
        
        logger.info(f"[WEBHOOK] Retrying amount input, attempt {attempt} for call {call_sid}")
        
        response = VoiceResponse()
        
        if attempt <= 3:
            _add_amount_gather_with_retry(response, attempt)
        else:
            response.say("Maximum attempts reached. Goodbye.", voice=VOICE)
            response.hangup()
            remove_active_call(call_sid)
        
        return str(response)
        
    except Exception as e:
        logger.error(f"[WEBHOOK] Retry amount error: {e}")
        response = VoiceResponse()
        response.say("An error occurred. Goodbye.", voice=VOICE)
        response.hangup()
        return str(response)


@app.route('/amount', methods=['POST'])
def handle_amount():
    """Handle user's SOL amount input and execute swap with GENERATIVE voice"""
    try:
        digits = request.form.get('Digits', '')
        call_sid = request.form.get('CallSid')
        attempt = int(request.args.get('attempt', '1'))
        
        logger.info(f"[WEBHOOK] Amount input: {digits} for call {call_sid} (attempt {attempt})")
        
        response = VoiceResponse()
        
        # Get call data from Redis
        call_data = get_active_call(call_sid)
        if not call_data:
            response.say("Session expired. Check Telegram for details. Goodbye.", voice=VOICE)
            response.hangup()
            return str(response)
        
        # Parse amount (e.g., "0*5" -> 0.5 SOL, "1*5" -> 1.5 SOL)
        try:
            amount_str = digits.replace('*', '.')
            amount_sol = float(amount_str)
            
            if amount_sol <= 0 or amount_sol > 100:
                if attempt < 3:
                    response.say(
                        "Amount must be between zero and one hundred SOL. Let me ask again.",
                        voice=VOICE
                    )
                    response.redirect(f'/retry-amount?attempt={attempt + 1}')
                else:
                    response.say("Invalid amount. Call ending. Goodbye.", voice=VOICE)
                    response.hangup()
                return str(response)
            
            # ✅ VALID AMOUNT - Confirm naturally with Generative voice
            # Format amount for natural speech
            if amount_sol == int(amount_sol):
                amount_speech = f"{int(amount_sol)} SOL"
            elif amount_sol < 1:
                # e.g., 0.5 -> "zero point 5 SOL"
                decimal_part = str(amount_sol).split('.')[1]
                amount_speech = f"zero point {decimal_part} SOL"
            else:
                # e.g., 1.5 -> "one point 5 SOL"
                amount_speech = f"{amount_sol} SOL"
            
            response.say(
                f"Perfect. Buying with {amount_speech}. "
                f"Processing your swap now. "
                f"You'll receive confirmation on Telegram. Goodbye.",
                voice=VOICE
            )
            response.hangup()
            
            # ✅ QUEUE SWAP TASK
            from tasks import execute_swap_task
            
            user_id = call_data["user_id"]
            token_address = call_data["token_data"]["id"]
            channel_name = call_data["channel_name"]
            
            execute_swap_task.apply_async(
                args=[user_id, token_address, amount_sol, channel_name],
                queue='urgent',
                priority=10
            )
            
            logger.info(f"[WEBHOOK] ✅ Queued swap: {amount_sol} SOL for user {user_id}")
            
            # Clean up
            remove_active_call(call_sid)
            
        except (ValueError, AttributeError) as e:
            logger.error(f"[WEBHOOK] Amount parse error: {e}")
            if attempt < 3:
                response.say(
                    "Could not understand the amount. Let me ask again.",
                    voice=VOICE
                )
                response.redirect(f'/retry-amount?attempt={attempt + 1}')
            else:
                response.say("Could not parse amount. Call ending. Goodbye.", voice=VOICE)
                response.hangup()
                remove_active_call(call_sid)
        
        return str(response)
        
    except Exception as e:
        logger.error(f"[WEBHOOK] Handle amount error: {e}", exc_info=True)
        response = VoiceResponse()
        response.say("An error occurred. Goodbye.", voice=VOICE)
        response.hangup()
        return str(response)


# ==================== STATUS CALLBACK ====================

@app.route('/call-status', methods=['POST'])
def call_status():
    """
    Handle call status updates (completed, failed, no-answer, busy)
    Clean up Redis for any call ending
    """
    try:
        call_sid = request.form.get('CallSid')
        status = request.form.get('CallStatus')
        
        logger.info(f"[WEBHOOK] Call {call_sid} status: {status}")
        
        # ✅ HANDLE ANY CALL ENDING - Clean up Redis
        if status in ['no-answer', 'failed', 'busy', 'completed', 'canceled']:
            remove_active_call(call_sid)
            logger.info(f"[WEBHOOK] ✅ Cleaned up call {call_sid} due to status: {status}")
        
        return '', 200
        
    except Exception as e:
        logger.error(f"[WEBHOOK] Call status error: {e}", exc_info=True)
        return '', 500

# ==================== PHONE VERIFICATION ENDPOINTS ====================

@app.route('/verify-code', methods=['POST'])
def handle_verify_code():
    """Handle verification code input from user"""
    try:
        digits = request.form.get('Digits', '')
        call_sid = request.form.get('CallSid')
        
        logger.info(f"[VERIFY] Received code: {digits} for call {call_sid}")
        
        response = VoiceResponse()
        
        # Get verification data from Redis
        verify_data = get_verification_data(call_sid)
        
        if not verify_data:
            response.say(
                "Verification session expired. Please request a new code in Telegram. Goodbye!",
                voice=VOICE
            )
            response.hangup()
            return str(response)
        
        # Compare codes
        stored_code = verify_data.get("code")
        telegram_id = verify_data.get("telegram_id")
        phone_number = verify_data.get("phone_number")
        
        if digits == stored_code:
            # SUCCESS
            logger.info(f"[VERIFY] ✅ Code matched for user {telegram_id}")
            
            response.say(
                "Thank you! Your phone number has been verified successfully. "
                "You will receive the confirmation in your Telegram. Goodbye!",
                voice=VOICE
            )
            response.hangup()
            
            # Store verification result in Redis for bot to process
            store_verification_result(call_sid, {
                "success": True,
                "telegram_id": telegram_id,
                "phone_number": phone_number
            })
            
        else:
            # FAILED
            logger.info(f"[VERIFY] ❌ Code mismatch for user {telegram_id}: entered {digits}, expected {stored_code}")
            
            response.say(
                "The code you entered is incorrect. "
                "Please check your Telegram and try again. Goodbye!",
                voice=VOICE
            )
            response.hangup()
            
            # Store verification result
            store_verification_result(call_sid, {
                "success": False,
                "telegram_id": telegram_id,
                "phone_number": phone_number,
                "error": "incorrect_code"
            })
        
        # Clean up verification data
        remove_verification_data(call_sid)
        
        return str(response)
        
    except Exception as e:
        logger.error(f"[VERIFY] Error: {e}", exc_info=True)
        response = VoiceResponse()
        response.say("An error occurred. Please try again later. Goodbye!", voice=VOICE)
        response.hangup()
        return str(response)


@app.route('/verify-call-status', methods=['POST'])
def verify_call_status():
    """Handle verification call status updates"""
    try:
        call_sid = request.form.get('CallSid')
        status = request.form.get('CallStatus')
        
        logger.info(f"[VERIFY] Call {call_sid} status: {status}")
        
        # Handle failed/no-answer/busy calls
        if status in ['no-answer', 'failed', 'busy']:
            verify_data = get_verification_data(call_sid)
            
            if verify_data:
                # Store failure result
                store_verification_result(call_sid, {
                    "success": False,
                    "telegram_id": verify_data.get("telegram_id"),
                    "phone_number": verify_data.get("phone_number"),
                    "error": f"call_{status}"
                })
                
                remove_verification_data(call_sid)
        
        return '', 200
        
    except Exception as e:
        logger.error(f"[VERIFY] Status error: {e}", exc_info=True)
        return '', 500


# ==================== VERIFICATION REDIS HELPERS ====================

def get_verification_data(call_sid: str):
    """Get verification data from Redis by call SID"""
    try:
        redis_key = f"verify_call:{call_sid}"
        data_json = redis_client.get(redis_key)
        
        if data_json:
            return json.loads(data_json)
        return None
    except Exception as e:
        logger.error(f"[VERIFY] Error getting verification data: {e}")
        return None


def remove_verification_data(call_sid: str):
    """Remove verification data from Redis"""
    try:
        redis_key = f"verify_call:{call_sid}"
        redis_client.delete(redis_key)
    except Exception as e:
        logger.error(f"[VERIFY] Error removing verification data: {e}")


def store_verification_result(call_sid: str, result: dict):
    """Store verification result for bot to process"""
    try:
        redis_key = f"verify_result:{call_sid}"
        redis_client.setex(
            redis_key,
            300,  # 5 minutes expiry
            json.dumps(result)
        )
        logger.info(f"[VERIFY] Stored result for {call_sid}: {result}")
    except Exception as e:
        logger.error(f"[VERIFY] Error storing result: {e}")

# ==================== MAIN ====================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)