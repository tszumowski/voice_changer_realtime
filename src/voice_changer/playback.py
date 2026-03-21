"""Audio playback thread - writes transformed audio to output device."""

from __future__ import annotations

import logging
import queue
import threading

import pyaudio

logger = logging.getLogger(__name__)


class AudioPlayback:
    """Plays audio chunks from a queue to the selected output device."""

    def __init__(
        self,
        output_queue: queue.Queue,
        sample_rate: int = 16000,
        device_index: int | None = None,
        chunk_size: int = 1024,
    ):
        self.output_queue = output_queue
        self.sample_rate = sample_rate
        self.device_index = device_index
        self.chunk_size = chunk_size
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pa: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None

    def start(self):
        """Start the playback thread."""
        self._pa = pyaudio.PyAudio()

        kwargs = {
            "format": pyaudio.paInt16,
            "channels": 1,
            "rate": self.sample_rate,
            "output": True,
            "frames_per_buffer": self.chunk_size,
        }
        if self.device_index is not None:
            kwargs["output_device_index"] = self.device_index

        self._stream = self._pa.open(**kwargs)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            "Playback started (rate=%d, device=%s)",
            self.sample_rate,
            self.device_index or "default",
        )

    def _run(self):
        """Playback loop - reads from queue and writes to stream."""
        while not self._stop_event.is_set():
            try:
                chunk = self.output_queue.get(timeout=0.1)
                if chunk is None:  # Poison pill
                    break
                self._stream.write(chunk)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("Playback error: %s", e)
                break

    def stop(self):
        """Stop playback and clean up."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        if self._pa:
            self._pa.terminate()
        logger.info("Playback stopped")
