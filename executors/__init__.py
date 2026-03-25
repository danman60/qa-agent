"""QA Agent Executors — pluggable platform backends for testing."""

from .base import BaseExecutor
from .web import WebExecutor
from .avd import AVDExecutor
from .device import DeviceExecutor

__all__ = ["BaseExecutor", "WebExecutor", "AVDExecutor", "DeviceExecutor"]
