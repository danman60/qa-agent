"""QA Agent Executors — pluggable platform backends for testing."""

from .base import BaseExecutor
from .web import WebExecutor

__all__ = ["BaseExecutor", "WebExecutor"]
