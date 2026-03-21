"""Resemble.ai Speech-to-Speech pipeline.

Uses Resemble's STS API which preserves prosody (like ElevenLabs STS)
but is a different provider. Requires WAV hosted at HTTPS URL.
"""

from __future__ import annotations

import base64
import io
import logging
import queue
import threading
import time
import wave
from pathlib import Path

import requests

from voice_changer.capture import MicCapture
from voice_changer.config import Settings
from voice_changer.playback import AudioPlayback
from voice_changer.transformer import _wrap_pcm_as_wav
from voice_changer.vad import VoiceActivityDetector

logger = logging.getLogger(__name__)

RESEMBLE_SYNTHESIZE_URL = "https://f.cluster.resemble.ai/synthesize"
TEMP_UPLOAD_URL = "https://0x0.st"


def _upload_temp_wav(wav_bytes: bytes) -> str:
    """Upload WAV to temporary file host, return HTTPS URL."""
    resp = requests.post(
        TEMP_UPLOAD_URL,
        files={"file": ("segment.wav", wav_bytes, "audio/wav")},
        timeout=10,
    )
    resp.raise_for_status()
    url = resp.text.strip()
    # Ensure HTTPS
    if url.startswith("http://"):
        url = url.replace("http://", "https://", 1)
    logger.debug("Uploaded WAV to: %s", url)
    return url


def _resemble_convert(
    api_key: str,
    wav_url: str,
    voice_uuid: str,
    sample_rate: int = 16000,
    model: str = "chatterbox-turbo",
) -> bytes:
    """Call Resemble STS API and return PCM audio bytes."""
    ssml = f'<speak><resemble:convert src="{wav_url}"></resemble:convert></speak>'

    payload = {
        "voice_uuid": voice_uuid,
        "data": ssml,
        "sample_rate": sample_rate,
        "output_format": "wav",
        "precision": "PCM_16",
        "model": model,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    start = time.monotonic()
    resp = requests.post(RESEMBLE_SYNTHESIZE_URL, json=payload, headers=headers, timeout=30)

    if resp.status_code != 200:
        error_msg = resp.text[:500]
        raise RuntimeError(f"Resemble API error ({resp.status_code}): {error_msg}")

    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Resemble API failed: {data}")

    latency = time.monotonic() - start
    synth_dur = data.get("synth_duration", 0)
    logger.info(
        "Resemble API latency: %.0fms (synth: %.0fms, duration: %.1fs)",
        latency * 1000,
        synth_dur * 1000 if synth_dur else 0,
        data.get("duration", 0),
    )

    # Decode base64 audio
    audio_b64 = data["audio_content"]
    audio_bytes = base64.b64decode(audio_b64)

    # The response is a full WAV file — extract raw PCM
    with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
        pcm_data = wf.readframes(wf.getnframes())

    return pcm_data


def list_resemble_voices(api_key: str) -> list[dict]:
    """List available Resemble.ai voices."""
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(
        "https://app.resemble.ai/api/v2/voices?page=1",
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    voices = []
    for v in data.get("items", []):
        voices.append({
            "voice_uuid": v.get("uuid", ""),
            "name": v.get("name", ""),
            "status": v.get("component_status", {}).get("speech_to_speech", "unknown"),
        })
    return voices


class ResemblePipeline:
    """Live voice changing pipeline using Resemble.ai STS."""

    def __init__(self, settings: Settings, ptt=None):
        self.settings = settings
        self.ptt = ptt

        if not settings.resemble_api_key:
            raise SystemExit(
                "Error: RESEMBLE_API_KEY not found.\n"
                "Set it in .env or pass via environment. See .env.example."
            )

        self.api_key = settings.resemble_api_key
        self.voice_uuid = settings.resemble_voice_uuid

        if not self.voice_uuid:
            # Try to get first available voice
            try:
                voices = list_resemble_voices(self.api_key)
                if voices:
                    self.voice_uuid = voices[0]["voice_uuid"]
                    logger.info("Using Resemble voice: %s (%s)", voices[0]["name"], self.voice_uuid)
                else:
                    raise SystemExit("Error: No Resemble.ai voices found.")
            except requests.RequestException as e:
                raise SystemExit(f"Error listing Resemble voices: {e}")

        self._capture_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._output_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=200)
        self._stop_event = threading.Event()
        self._playing_event = threading.Event()

        self._capture = MicCapture(
            frame_queue=self._capture_queue,
            sample_rate=settings.sample_rate,
            frame_size=settings.frame_size,
            device_index=settings.input_device,
        )
        self._playback = AudioPlayback(
            output_queue=self._output_queue,
            sample_rate=settings.sample_rate,
            device_index=settings.output_device,
            playing_event=self._playing_event,
        )
        self._vad = VoiceActivityDetector(
            sample_rate=settings.sample_rate,
            frame_duration_ms=settings.chunk_duration_ms,
            aggressiveness=settings.vad_aggressiveness,
            silence_end_frames=settings.silence_frames,
            max_segment_frames=settings.max_segment_frames,
        )

        self._segments_processed = 0
        self._errors = 0
        self._start_time = 0.0

    def start(self):
        """Start the Resemble pipeline."""
        logger.info("Starting Resemble pipeline (voice=%s)", self.voice_uuid)
        print(f"\nResemble.ai voice changer active! Voice: {self.voice_uuid}")
        print("Mode: Resemble.ai STS (preserves prosody)")

        if self.ptt:
            from voice_changer.ptt import _key_display_name
            key_name = _key_display_name(self.ptt.key)
            print(f"Push-to-talk: hold [{key_name}] to record, release to send.")
            self.ptt.start()
        else:
            print("Speak into your microphone. (Half-duplex: mic muted during playback)")
        print("Press Ctrl+C to stop.\n")

        self._start_time = time.monotonic()
        self._capture.start()
        self._playback.start()

        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as executor:
                self._process_loop(executor)
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            self.stop()

    def _process_loop(self, executor):
        """Main loop: capture → VAD → transform → playback."""
        was_playing = False
        ptt_was_active = False

        while not self._stop_event.is_set():
            try:
                frame = self._capture_queue.get(timeout=0.1)
            except queue.Empty:
                if self.ptt and ptt_was_active and not self.ptt.is_active:
                    segment = self._vad.flush()
                    if segment is not None:
                        executor.submit(self._transform_and_play, segment)
                    ptt_was_active = False
                continue

            # PTT mode
            if self.ptt:
                if self.ptt.is_active:
                    ptt_was_active = True
                    segment = self._vad.process_frame(frame)
                    if segment is not None:
                        executor.submit(self._transform_and_play, segment)
                else:
                    if ptt_was_active:
                        segment = self._vad.flush()
                        if segment is not None:
                            executor.submit(self._transform_and_play, segment)
                        ptt_was_active = False
                continue

            # Auto mode with feedback suppression
            if self._playing_event.is_set():
                if not was_playing:
                    was_playing = True
                continue

            if was_playing:
                self._vad.reset()
                while not self._capture_queue.empty():
                    try:
                        self._capture_queue.get_nowait()
                    except queue.Empty:
                        break
                was_playing = False
                continue

            segment = self._vad.process_frame(frame)
            if segment is not None:
                executor.submit(self._transform_and_play, segment)

    def _transform_and_play(self, segment: bytes):
        """Transform via Resemble and push to playback."""
        try:
            # Wrap as WAV
            wav_data = _wrap_pcm_as_wav(segment, sample_rate=self.settings.sample_rate)

            # Upload to temp host
            url = _upload_temp_wav(wav_data)

            # Call Resemble API
            pcm_output = _resemble_convert(
                api_key=self.api_key,
                wav_url=url,
                voice_uuid=self.voice_uuid,
                sample_rate=self.settings.sample_rate,
            )

            self._output_queue.put(pcm_output)
            self._segments_processed += 1

        except Exception as e:
            logger.error("Resemble transform error: %s", e)
            self._errors += 1

    def stop(self):
        """Stop all components."""
        self._stop_event.set()
        if self.ptt:
            self.ptt.stop()
        self._capture.stop()
        self._output_queue.put(None)
        self._playback.stop()

        elapsed = time.monotonic() - self._start_time
        print(f"\nSession stats (Resemble mode):")
        print(f"  Duration: {elapsed:.1f}s")
        print(f"  Segments processed: {self._segments_processed}")
        print(f"  Errors: {self._errors}")


def run_resemble_test(
    settings: Settings,
    input_file: str,
    output_file: str = "output.wav",
) -> Path:
    """File-based test for Resemble mode."""
    input_path = Path(input_file)
    output_path = Path(output_file)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not settings.resemble_api_key:
        raise SystemExit("Error: RESEMBLE_API_KEY not set in .env")

    # Read input WAV
    with wave.open(str(input_path), "rb") as wf:
        input_rate = wf.getframerate()
        pcm_data = wf.readframes(wf.getnframes())

    input_duration = len(pcm_data) / (input_rate * 2)
    logger.info("Input: %s (%.1fs, %dHz)", input_path.name, input_duration, input_rate)

    # Resample if needed
    if input_rate != settings.sample_rate:
        from voice_changer.pipeline import _resample_pcm
        pcm_data = _resample_pcm(pcm_data, input_rate, settings.sample_rate)

    # Wrap as WAV and upload
    print("Step 1/3: Uploading audio...")
    wav_data = _wrap_pcm_as_wav(pcm_data, sample_rate=settings.sample_rate)
    url = _upload_temp_wav(wav_data)
    logger.info("Uploaded to: %s", url)

    # Get voice UUID
    voice_uuid = settings.resemble_voice_uuid
    if not voice_uuid:
        voices = list_resemble_voices(settings.resemble_api_key)
        if not voices:
            raise SystemExit("No Resemble.ai voices found.")
        voice_uuid = voices[0]["voice_uuid"]
        print(f"  Using voice: {voices[0]['name']} ({voice_uuid})")

    # Call Resemble API
    print("Step 2/3: Converting voice (Resemble.ai)...")
    pcm_output = _resemble_convert(
        api_key=settings.resemble_api_key,
        wav_url=url,
        voice_uuid=voice_uuid,
        sample_rate=settings.sample_rate,
    )

    # Write output
    print("Step 3/3: Writing output...")
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(settings.sample_rate)
        wf.writeframes(pcm_output)

    out_duration = len(pcm_output) / (settings.sample_rate * 2)
    print(f"\nResemble test complete!")
    print(f"  Input:  {input_path} ({input_duration:.1f}s)")
    print(f"  Output: {output_path} ({out_duration:.1f}s)")

    return output_path
