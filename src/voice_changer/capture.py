"""Microphone capture thread using PyAudio callback mode."""

from __future__ import annotations

import logging
import queue
import threading

import pyaudio

logger = logging.getLogger(__name__)


class MicCapture:
    """Captures audio from the microphone and pushes frames to a queue."""

    def __init__(
        self,
        frame_queue: queue.Queue,
        sample_rate: int = 16000,
        frame_size: int = 480,  # 30ms at 16kHz
        device_index: int | None = None,
    ):
        self.frame_queue = frame_queue
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        self.device_index = device_index
        self._stop_event = threading.Event()
        self._pa: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None

    def _callback(self, in_data, frame_count, time_info, status):
        """PyAudio callback - runs in a C thread, must be fast."""
        if status:
            logger.warning("PyAudio callback status: %s", status)
        if self._stop_event.is_set():
            return (None, pyaudio.paComplete)
        try:
            self.frame_queue.put_nowait(in_data)
        except queue.Full:
            logger.warning("Capture queue full, dropping frame")
        return (None, pyaudio.paContinue)

    def start(self):
        """Open the mic stream."""
        self._pa = pyaudio.PyAudio()

        kwargs = {
            "format": pyaudio.paInt16,
            "channels": 1,
            "rate": self.sample_rate,
            "input": True,
            "frames_per_buffer": self.frame_size,
            "stream_callback": self._callback,
        }
        if self.device_index is not None:
            kwargs["input_device_index"] = self.device_index

        self._stream = self._pa.open(**kwargs)
        self._stream.start_stream()
        logger.info(
            "Mic capture started (rate=%d, frame=%d, device=%s)",
            self.sample_rate,
            self.frame_size,
            self.device_index or "default",
        )

    def stop(self):
        """Stop and clean up."""
        self._stop_event.set()
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa:
            self._pa.terminate()
            self._pa = None
        logger.info("Mic capture stopped")
