"""Voice Activity Detection with segment assembly.

State machine: IDLE -> SPEAKING -> TRAILING_SILENCE -> emit segment -> IDLE
"""

from __future__ import annotations

import logging
from enum import Enum, auto

import webrtcvad

logger = logging.getLogger(__name__)


class State(Enum):
    IDLE = auto()
    SPEAKING = auto()
    TRAILING_SILENCE = auto()


class VoiceActivityDetector:
    """Accumulates PCM frames and emits speech segments."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        aggressiveness: int = 2,
        speech_start_frames: int = 8,
        silence_end_frames: int = 10,
        max_segment_frames: int = 67,  # ~2s at 30ms
    ):
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.speech_start_frames = speech_start_frames
        self.silence_end_frames = silence_end_frames
        self.max_segment_frames = max_segment_frames

        self.state = State.IDLE
        self._buffer: list[bytes] = []
        self._speech_count = 0
        self._silence_count = 0
        self._segment_frames = 0

    def reset(self):
        """Reset to idle state."""
        self.state = State.IDLE
        self._buffer.clear()
        self._speech_count = 0
        self._silence_count = 0
        self._segment_frames = 0

    def process_frame(self, frame: bytes) -> bytes | None:
        """Process a single PCM frame. Returns a segment (bytes) when ready, else None.

        Args:
            frame: Raw PCM 16-bit LE mono audio, exactly frame_duration_ms long.

        Returns:
            Concatenated PCM bytes of a speech segment, or None.
        """
        is_speech = self.vad.is_speech(frame, self.sample_rate)

        if self.state == State.IDLE:
            if is_speech:
                self._speech_count += 1
                self._buffer.append(frame)
                if self._speech_count >= self.speech_start_frames:
                    self.state = State.SPEAKING
                    self._segment_frames = len(self._buffer)
                    logger.debug("VAD: IDLE -> SPEAKING")
            else:
                self._speech_count = 0
                self._buffer.clear()

        elif self.state == State.SPEAKING:
            self._buffer.append(frame)
            self._segment_frames += 1

            if not is_speech:
                self._silence_count += 1
                self.state = State.TRAILING_SILENCE
            elif self._segment_frames >= self.max_segment_frames:
                logger.debug("VAD: segment full (%d frames)", self._segment_frames)
                return self._emit_segment()

        elif self.state == State.TRAILING_SILENCE:
            self._buffer.append(frame)
            self._segment_frames += 1

            if is_speech:
                self._silence_count = 0
                self.state = State.SPEAKING
            else:
                self._silence_count += 1
                if self._silence_count >= self.silence_end_frames:
                    logger.debug(
                        "VAD: silence detected, emitting segment (%d frames)",
                        self._segment_frames,
                    )
                    return self._emit_segment()

            if self._segment_frames >= self.max_segment_frames:
                logger.debug("VAD: segment full during silence (%d frames)", self._segment_frames)
                return self._emit_segment()

        return None

    def flush(self) -> bytes | None:
        """Flush any remaining buffered audio as a segment."""
        if self._buffer and self.state != State.IDLE:
            return self._emit_segment()
        return None

    def _emit_segment(self) -> bytes:
        """Emit the current buffer as a segment and reset."""
        segment = b"".join(self._buffer)
        self.reset()
        return segment
