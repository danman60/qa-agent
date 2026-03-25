"""Abstract base executor interface for QA Agent.

All platform executors (web, AVD, device) implement this interface.
The harness in qa_agent.py calls only these methods — it never touches
Playwright or ADB directly.
"""

from abc import ABC, abstractmethod


class BaseExecutor(ABC):
    """Platform-agnostic test executor interface."""

    @abstractmethod
    def setup(self) -> bool:
        """Initialize the executor (launch browser, connect device, etc).
        Returns True on success."""
        ...

    @abstractmethod
    def teardown(self) -> None:
        """Clean up resources."""
        ...

    @abstractmethod
    def navigate(self, target: str) -> bool:
        """Navigate to a URL or Android activity.
        Returns True on success."""
        ...

    @abstractmethod
    def snapshot(self) -> str:
        """Get the current screen state as text.
        Web: accessibility tree. Android: uiautomator XML or parsed text."""
        ...

    @abstractmethod
    def screenshot(self, path: str) -> str:
        """Take a screenshot and save to path. Returns the path."""
        ...

    @abstractmethod
    def click(self, role: str, name: str) -> tuple[bool, str]:
        """Click an element by role and name. Returns (success, detail)."""
        ...

    @abstractmethod
    def click_text(self, text: str) -> tuple[bool, str]:
        """Click by visible text (fallback). Returns (success, detail)."""
        ...

    @abstractmethod
    def fill(self, role: str, name: str, value: str) -> tuple[bool, str]:
        """Fill a form field. Returns (success, detail)."""
        ...

    @abstractmethod
    def type_text(self, text: str) -> tuple[bool, str]:
        """Type into currently focused element. Returns (success, detail)."""
        ...

    @abstractmethod
    def press_key(self, key: str) -> tuple[bool, str]:
        """Press a keyboard key. Returns (success, detail)."""
        ...

    @abstractmethod
    def scroll(self, direction: str = "down") -> tuple[bool, str]:
        """Scroll up or down. Returns (success, detail)."""
        ...

    @abstractmethod
    def select_option(self, role: str, name: str, value: str) -> tuple[bool, str]:
        """Select from a dropdown. Returns (success, detail)."""
        ...

    @abstractmethod
    def back(self) -> tuple[bool, str]:
        """Go back (browser back or KEYCODE_BACK). Returns (success, detail)."""
        ...

    @abstractmethod
    def get_console_errors(self, since_last: bool = True) -> list[dict]:
        """Get console/logcat errors. Optionally clear after reading."""
        ...

    @abstractmethod
    def get_network_errors(self, since_last: bool = True) -> list[dict]:
        """Get network errors. Optionally clear after reading."""
        ...

    @abstractmethod
    def discover_links(self, snapshot_text: str) -> dict:
        """Extract navigation links from snapshot. Returns {name: True}."""
        ...

    @abstractmethod
    def set_viewport(self, width: int, height: int) -> None:
        """Set viewport/screen size."""
        ...

    @property
    @abstractmethod
    def url(self) -> str:
        """Current URL or activity name."""
        ...

    @abstractmethod
    def get_screen_size(self) -> tuple[int, int]:
        """Returns (width, height) of current viewport/screen."""
        ...
