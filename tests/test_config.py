"""Tests for config module."""

import os
from unittest.mock import patch

import pytest

from voice_changer.config import Settings, load_settings


def test_settings_frame_size():
    s = Settings(api_key="test", sample_rate=16000, chunk_duration_ms=30)
    assert s.frame_size == 480
    assert s.frame_bytes == 960


def test_settings_max_segment_frames():
    s = Settings(api_key="test", segment_duration_s=1.5, chunk_duration_ms=30)
    assert s.max_segment_frames == 50  # 1500ms / 30ms


def test_settings_silence_frames():
    s = Settings(api_key="test", vad_silence_duration_s=0.25, chunk_duration_ms=30)
    assert s.silence_frames == 8  # 250ms / 30ms


def test_load_settings_missing_key(monkeypatch, tmp_path):
    # Point dotenv away from real .env so it doesn't load the real key
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with patch("voice_changer.config.load_dotenv"):
        with pytest.raises(SystemExit, match="ELEVENLABS_API_KEY"):
            load_settings()


def test_load_settings_from_env():
    with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test_key_123"}):
        s = load_settings()
        assert s.api_key == "test_key_123"


def test_load_settings_overrides():
    with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test"}):
        s = load_settings(voice_id="my_voice", segment_duration_s=3.0)
        assert s.voice_id == "my_voice"
        assert s.segment_duration_s == 3.0
