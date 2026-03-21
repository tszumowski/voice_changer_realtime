"""PyAudio device discovery and selection helpers."""

from __future__ import annotations

import pyaudio


def list_devices(direction: str | None = None) -> list[dict]:
    """List audio devices. direction: 'input', 'output', or None for all."""
    p = pyaudio.PyAudio()
    devices = []
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if direction == "input" and info["maxInputChannels"] == 0:
                continue
            if direction == "output" and info["maxOutputChannels"] == 0:
                continue
            devices.append({
                "index": i,
                "name": info["name"],
                "input_channels": info["maxInputChannels"],
                "output_channels": info["maxOutputChannels"],
                "default_sample_rate": info["defaultSampleRate"],
            })
    finally:
        p.terminate()
    return devices


def find_device_by_name(pattern: str, direction: str = "output") -> int | None:
    """Find first device whose name contains `pattern` (case-insensitive)."""
    pattern_lower = pattern.lower()
    for dev in list_devices(direction):
        if pattern_lower in dev["name"].lower():
            return dev["index"]
    return None


def get_default_input() -> int:
    """Get the system default input device index."""
    p = pyaudio.PyAudio()
    try:
        return p.get_default_input_device_info()["index"]
    finally:
        p.terminate()


def get_default_output() -> int:
    """Get the system default output device index."""
    p = pyaudio.PyAudio()
    try:
        return p.get_default_output_device_info()["index"]
    finally:
        p.terminate()


def print_devices(direction: str | None = None) -> None:
    """Pretty-print audio devices."""
    devices = list_devices(direction)
    if not devices:
        print("No audio devices found.")
        return

    print(f"\n{'Idx':>4}  {'Name':<45} {'In':>3} {'Out':>4} {'Rate':>7}")
    print("-" * 70)
    for d in devices:
        print(
            f"{d['index']:>4}  {d['name']:<45} "
            f"{d['input_channels']:>3} {d['output_channels']:>4} "
            f"{d['default_sample_rate']:>7.0f}"
        )
    print()

    # Highlight useful devices
    blackhole = find_device_by_name("blackhole", "output")
    multi_out = find_device_by_name("multi-output", "output")
    if blackhole is not None:
        print(f"  BlackHole detected at index {blackhole}")
    if multi_out is not None:
        print(f"  Multi-Output Device detected at index {multi_out}")
    if blackhole is None and multi_out is None:
        print("  No BlackHole or Multi-Output Device found.")
        print("  See README.md for setup instructions.")
