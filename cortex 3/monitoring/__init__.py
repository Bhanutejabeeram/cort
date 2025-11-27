"""
Cortex Monitoring Module
Channel monitoring and signal detection
"""

from .channel_monitor import ChannelMonitor, channel_monitor_instance

__all__ = [
    'ChannelMonitor',
    'channel_monitor_instance'
]