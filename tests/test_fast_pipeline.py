"""Tests for the fast pipeline (STT → TTS)."""

import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest


def test_fast_pipeline_file_test_with_mocks(tmp_path):
    """Test the fast mode file pipeline with mocked STT and TTS."""
    from voice_changer.config import Settings

    # Create a test WAV with a sine wave
    sample_rate = 16000
    duration = 1.0
    t = np.arange(int(sample_rate * duration)) / sample_rate
    samples = (np.sin(2 * np.pi * 300 * t) * 16000).astype(np.int16)

    input_path = tmp_path / "test_input.wav"
    with wave.open(str(input_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    output_path = tmp_path / "test_output.wav"

    settings = Settings(api_key="test_key", voice_id="test_voice", mode="fast")

    # Mock the async transcription function
    fake_transcript = "hello this is a test"

    # Mock TTS to return fake audio
    fake_audio = (np.sin(2 * np.pi * 200 * t) * 8000).astype(np.int16).tobytes()

    with patch("voice_changer.fast_pipeline._transcribe_audio", return_value=fake_transcript) as mock_stt, \
         patch("voice_changer.fast_pipeline.get_default_voice_id", return_value="fake_voice"), \
         patch("voice_changer.fast_pipeline.ElevenLabs") as mock_client_cls:

        # Set up the mock TTS client
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.text_to_speech.convert_realtime.return_value = iter([fake_audio])

        from voice_changer.fast_pipeline import run_fast_test
        result = run_fast_test(settings, str(input_path), str(output_path))

    assert result.exists()
    with wave.open(str(result), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        out_duration = wf.getnframes() / wf.getframerate()
        assert out_duration > 0.1


def test_fast_pipeline_tts_params(tmp_path):
    """Verify TTS is called with correct parameters."""
    from voice_changer.config import Settings

    sample_rate = 16000
    t = np.arange(int(sample_rate * 0.5)) / sample_rate
    samples = (np.sin(2 * np.pi * 300 * t) * 16000).astype(np.int16)

    input_path = tmp_path / "input.wav"
    with wave.open(str(input_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    output_path = tmp_path / "output.wav"
    settings = Settings(api_key="test_key", voice_id="my_voice", mode="fast")

    fake_audio = b"\x00\x00" * 1000

    with patch("voice_changer.fast_pipeline._transcribe_audio", return_value="test"), \
         patch("voice_changer.fast_pipeline.get_default_voice_id", return_value="fallback"), \
         patch("voice_changer.fast_pipeline.ElevenLabs") as mock_cls:

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.text_to_speech.convert_realtime.return_value = iter([fake_audio])

        from voice_changer.fast_pipeline import run_fast_test
        run_fast_test(settings, str(input_path), str(output_path))

    # Verify TTS was called with the right voice and model
    call_kwargs = mock_client.text_to_speech.convert_realtime.call_args
    assert call_kwargs.kwargs["voice_id"] == "my_voice"
    assert call_kwargs.kwargs["model_id"] == "eleven_flash_v2_5"
    assert call_kwargs.kwargs["output_format"] == "pcm_16000"
