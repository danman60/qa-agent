"""QA Agent Executors — pluggable platform backends for testing."""

from .base import BaseExecutor
from .web import WebExecutor
from .avd import AVDExecutor

__all__ = ["BaseExecutor", "WebExecutor", "AVDExecutor"]
