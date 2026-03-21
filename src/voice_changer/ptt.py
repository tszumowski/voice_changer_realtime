"""Push-to-talk: hold a key to record, release to stop."""

from __future__ import annotations

import logging
import threading

from pynput import keyboard

logger = logging.getLogger(__name__)

# Default PTT key — right command key. Spacebar would interfere with typing.
DEFAULT_PTT_KEY = keyboard.Key.right


class PushToTalk:
    """Monitors a key press to control recording state.

    When the key is held down, `is_active` is True (recording).
    When released, `is_active` is False (not recording).
    """

    def __init__(self, key=DEFAULT_PTT_KEY):
        self.key = key
        self._active = threading.Event()
        self._listener: keyboard.Listener | None = None

    @property
    def is_active(self) -> bool:
        return self._active.is_set()

    def start(self):
        """Start listening for the PTT key."""
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        key_name = _key_display_name(self.key)
        logger.info("Push-to-talk enabled (hold %s to record)", key_name)

    def stop(self):
        """Stop the key listener."""
        if self._listener:
            self._listener.stop()
            self._listener = None

    def _on_press(self, key):
        if key == self.key:
            if not self._active.is_set():
                self._active.set()
                logger.debug("PTT: key pressed — recording")

    def _on_release(self, key):
        if key == self.key:
            if self._active.is_set():
                self._active.clear()
                logger.debug("PTT: key released — stopped")


def parse_ptt_key(key_str: str) -> keyboard.Key | keyboard.KeyCode:
    """Parse a key name string into a pynput key object.

    Supports: 'space', 'right', 'right_cmd', 'right_ctrl', 'right_shift',
    'left_cmd', 'f1'-'f20', or single characters like 'v', 'b', etc.
    """
    key_str = key_str.lower().strip()

    # Map common names to pynput keys
    key_map = {
        "space": keyboard.Key.space,
        "right": keyboard.Key.right,
        "right_cmd": keyboard.Key.cmd_r,
        "right_ctrl": keyboard.Key.ctrl_r,
        "right_shift": keyboard.Key.shift_r,
        "left_cmd": keyboard.Key.cmd,
        "left_ctrl": keyboard.Key.ctrl,
        "left_shift": keyboard.Key.shift,
        "right_alt": keyboard.Key.alt_r,
        "left_alt": keyboard.Key.alt,
        "caps_lock": keyboard.Key.caps_lock,
        "tab": keyboard.Key.tab,
    }

    if key_str in key_map:
        return key_map[key_str]

    # Function keys: f1-f20
    if key_str.startswith("f") and key_str[1:].isdigit():
        n = int(key_str[1:])
        if 1 <= n <= 20:
            return getattr(keyboard.Key, f"f{n}")

    # Single character
    if len(key_str) == 1:
        return keyboard.KeyCode.from_char(key_str)

    raise ValueError(
        f"Unknown key: '{key_str}'. Use 'space', 'right_cmd', 'f1'-'f20', "
        f"or a single character like 'v'."
    )


def _key_display_name(key) -> str:
    """Get a human-readable name for a key."""
    if isinstance(key, keyboard.Key):
        return key.name.replace("_", " ").title()
    if isinstance(key, keyboard.KeyCode):
        return f"'{key.char}'"
    return str(key)
