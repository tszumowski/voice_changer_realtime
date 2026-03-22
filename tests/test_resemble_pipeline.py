"""Tests for the Resemble.ai pipeline."""

import base64
import json
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from voice_changer.resemble_pipeline import _resemble_convert, _upload_temp_wav


def test_upload_temp_wav():
    """Test that temp upload sends correct data and returns URL."""
    fake_wav = b"RIFF" + b"\x00" * 100

    mock_resp = MagicMock()
    mock_resp.text = "https://litter.catbox.moe/abc123.wav\n"
    mock_resp.raise_for_status = MagicMock()

    with patch("voice_changer.resemble_pipeline.requests.post", return_value=mock_resp) as mock_post:
        url = _upload_temp_wav(fake_wav)

    assert url == "https://litter.catbox.moe/abc123.wav"
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "files" in call_kwargs.kwargs


def test_upload_temp_wav_returns_https():
    """Test that returned URLs are HTTPS."""
    mock_resp = MagicMock()
    mock_resp.text = "https://litter.catbox.moe/abc.wav\n"
    mock_resp.raise_for_status = MagicMock()

    with patch("voice_changer.resemble_pipeline.requests.post", return_value=mock_resp):
        url = _upload_temp_wav(b"test")

    assert url.startswith("https://")


def test_resemble_convert_builds_correct_ssml():
    """Test that the SSML payload is correctly formatted."""
    # Create a small valid WAV for the response
    sample_rate = 16000
    samples = np.zeros(1600, dtype=np.int16)
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    wav_bytes = buf.getvalue()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "success": True,
        "audio_content": base64.b64encode(wav_bytes).decode(),
        "duration": 0.1,
        "synth_duration": 0.05,
    }

    with patch("voice_changer.resemble_pipeline.requests.post", return_value=mock_resp) as mock_post:
        result = _resemble_convert(
            api_key="test_key",
            wav_url="https://example.com/audio.wav",
            voice_uuid="test_voice_uuid",
            sample_rate=16000,
        )

    # Verify the request payload
    call_args = mock_post.call_args
    payload = call_args.kwargs["json"]
    assert payload["voice_uuid"] == "test_voice_uuid"
    assert '<resemble:convert src="https://example.com/audio.wav">' in payload["data"]
    assert payload["sample_rate"] == 16000
    assert payload["precision"] == "PCM_16"

    # Verify headers
    headers = call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test_key"

    # Verify output is PCM bytes
    assert isinstance(result, bytes)
    assert len(result) > 0


def test_resemble_convert_handles_api_error():
    """Test that API errors are raised properly."""
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = '{"error": "bad request"}'

    with patch("voice_changer.resemble_pipeline.requests.post", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="Resemble API error"):
            _resemble_convert("key", "https://example.com/a.wav", "voice", 16000)


def test_resemble_file_test_with_mocks(tmp_path):
    """Test the full Resemble file-based pipeline with mocks."""
    from voice_changer.config import Settings

    # Create test WAV
    sample_rate = 16000
    samples = (np.sin(2 * np.pi * 300 * np.arange(16000) / 16000) * 16000).astype(np.int16)

    input_path = tmp_path / "input.wav"
    with wave.open(str(input_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    output_path = tmp_path / "output.wav"

    settings = Settings(
        api_key="test",
        mode="resemble",
        resemble_api_key="test_resemble_key",
        resemble_voice_uuid="test_voice",
    )

    # Create fake output WAV
    import io
    buf = io.BytesIO()
    fake_output = np.zeros(8000, dtype=np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(fake_output.tobytes())
    fake_wav = buf.getvalue()

    # Mock upload and API
    with patch("voice_changer.resemble_pipeline._upload_temp_wav", return_value="https://example.com/a.wav"), \
         patch("voice_changer.resemble_pipeline._resemble_convert", return_value=fake_output.tobytes()):

        from voice_changer.resemble_pipeline import run_resemble_test
        result = run_resemble_test(settings, str(input_path), str(output_path))

    assert result.exists()
    with wave.open(str(result), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
