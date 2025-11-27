"""
Cortex Prompts Module
AI prompts and response formatting
"""

from .ai_prompts import (
    get_system_prompt,
    get_context_guide,
    SIGNAL_CLASSIFICATION_PROMPT,
    CALL_SCRIPT_TEMPLATE,
    GROUP_CONTEXT_MARKERS
)

__all__ = [
    'get_system_prompt',
    'get_context_guide',
    'SIGNAL_CLASSIFICATION_PROMPT',
    'CALL_SCRIPT_TEMPLATE',
    'GROUP_CONTEXT_MARKERS'
]