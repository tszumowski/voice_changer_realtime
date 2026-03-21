"""Tests for VAD module."""

import struct

import numpy as np

from voice_changer.vad import State, VoiceActivityDetector


def _make_silence_frame(frame_size: int = 480) -> bytes:
    """Generate a silent frame (all zeros)."""
    return b"\x00" * (frame_size * 2)


def _make_speech_frame(frame_size: int = 480, freq: float = 440.0, sample_rate: int = 16000) -> bytes:
    """Generate a frame with a tone (simulates speech for VAD)."""
    t = np.arange(frame_size) / sample_rate
    samples = (np.sin(2 * np.pi * freq * t) * 16000).astype(np.int16)
    return samples.tobytes()


def test_vad_starts_idle():
    vad = VoiceActivityDetector()
    assert vad.state == State.IDLE


def test_vad_silence_stays_idle():
    vad = VoiceActivityDetector()
    for _ in range(100):
        result = vad.process_frame(_make_silence_frame())
        assert result is None
    assert vad.state == State.IDLE


def test_vad_speech_triggers_speaking():
    vad = VoiceActivityDetector(speech_start_frames=3)
    for _ in range(10):
        vad.process_frame(_make_speech_frame())
    assert vad.state in (State.SPEAKING, State.TRAILING_SILENCE)


def test_vad_emits_segment_on_silence_after_speech():
    vad = VoiceActivityDetector(
        speech_start_frames=3,
        silence_end_frames=5,
        max_segment_frames=200,
    )
    # Feed speech frames
    for _ in range(20):
        vad.process_frame(_make_speech_frame())

    # Feed silence frames until segment emitted
    segment = None
    for _ in range(20):
        result = vad.process_frame(_make_silence_frame())
        if result is not None:
            segment = result
            break

    assert segment is not None
    assert len(segment) > 0
    assert vad.state == State.IDLE


def test_vad_emits_segment_on_max_frames():
    vad = VoiceActivityDetector(
        speech_start_frames=3,
        max_segment_frames=10,
    )
    segment = None
    for _ in range(20):
        result = vad.process_frame(_make_speech_frame())
        if result is not None:
            segment = result
            break

    assert segment is not None
    assert vad.state == State.IDLE


def test_vad_flush():
    vad = VoiceActivityDetector(speech_start_frames=3, max_segment_frames=200)
    for _ in range(10):
        vad.process_frame(_make_speech_frame())

    segment = vad.flush()
    assert segment is not None
    assert len(segment) > 0
    assert vad.state == State.IDLE


def test_vad_flush_when_idle():
    vad = VoiceActivityDetector()
    assert vad.flush() is None


def test_vad_reset():
    vad = VoiceActivityDetector(speech_start_frames=3)
    for _ in range(10):
        vad.process_frame(_make_speech_frame())
    vad.reset()
    assert vad.state == State.IDLE
