"""
Celery Configuration for Cortex Bot
Handles background task processing with SSL support for Upstash
FIXED: Removed circular import issue
"""

import os
import ssl
from celery import Celery
from kombu import Queue, Exchange
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get Redis URL
REDIS_URL = os.getenv("REDIS_URL")

if not REDIS_URL:
    raise ValueError("REDIS_URL not found in environment variables. Please add it to your .env file")

print(f"[CELERY CONFIG] Redis URL: {REDIS_URL[:30]}...")

# Create Celery application
celery_app = Celery(
    'cortex_bot',
    broker=REDIS_URL,
    backend=REDIS_URL
)

# SSL Configuration for Upstash (rediss://)
broker_use_ssl = {
    'ssl_cert_reqs': ssl.CERT_NONE  # Don't verify SSL certificates (safe for Upstash)
}

redis_backend_use_ssl = {
    'ssl_cert_reqs': ssl.CERT_NONE  # Don't verify SSL certificates
}

# Configure Celery
celery_app.conf.update(
    # SSL Settings for Redis
    broker_use_ssl=broker_use_ssl,
    redis_backend_use_ssl=redis_backend_use_ssl,
    
    # Serialization
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    
    # Timezone
    timezone='UTC',
    enable_utc=True,
    
    # Task routing - different queues for different priorities
    task_routes={
        'tasks.make_call_task': {'queue': 'urgent'},           # Phone calls - HIGHEST priority
        'tasks.execute_swap_task': {'queue': 'urgent'},        # Swaps - HIGH priority
        'tasks.send_notification_task': {'queue': 'low'},      # Notifications - LOW priority
    },
    
    # Define queues with priorities
    task_queues=(
        Queue('urgent', Exchange('urgent'), routing_key='urgent',
              queue_arguments={'x-max-priority': 10}),
        Queue('normal', Exchange('normal'), routing_key='normal',
              queue_arguments={'x-max-priority': 5}),
        Queue('low', Exchange('low'), routing_key='low',
              queue_arguments={'x-max-priority': 1}),
    ),
    
    # Worker settings
    task_acks_late=True,                    # Acknowledge task after completion
    task_reject_on_worker_lost=True,       # Requeue task if worker dies
    worker_prefetch_multiplier=1,           # Take one task at a time (fair distribution)
    
    # Result settings
    result_expires=3600,                    # Keep results for 1 hour
    
    # Retry settings
    task_default_retry_delay=10,            # Wait 10 seconds before retry
    task_max_retries=3,                     # Max 3 retries
    
    # Logging
    worker_hijack_root_logger=False,        # Don't override our logging
    worker_log_format='[%(asctime)s: %(levelname)s/%(processName)s] %(message)s',
    
    # CRITICAL FIX: Enable imports to work properly
    imports=('tasks',),  # Changed from include to imports
)

print("[CELERY CONFIG] ✅ Celery app configured successfully with SSL support")