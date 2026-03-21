"""Fast pipeline: ElevenLabs Scribe v2 STT (WebSocket) → Flash v2.5 TTS (WebSocket).

Trades prosody preservation for speed (~250-300ms vs ~1s).
Speech is transcribed to text then re-synthesized in the target voice.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import queue
import threading
import time
import wave
from pathlib import Path

from elevenlabs import AsyncElevenLabs, ElevenLabs
from elevenlabs.realtime.scribe import AudioFormat, CommitStrategy

from voice_changer.capture import MicCapture
from voice_changer.config import Settings
from voice_changer.playback import AudioPlayback
from voice_changer.transformer import get_default_voice_id

logger = logging.getLogger(__name__)

# Sentinel to signal end of stream
_SENTINEL = object()


class FastPipeline:
    """Real-time voice changing via STT → TTS WebSocket pipeline.

    Much lower latency than STS, but loses prosody/intonation from original speech.
    The caller hears the right words in the target voice, but with AI-generated delivery.
    """

    def __init__(self, settings: Settings, ptt=None):
        self.settings = settings
        self.ptt = ptt

        # Create sync client for TTS and voice lookup
        self._sync_client = ElevenLabs(api_key=settings.api_key)
        self.voice_id = settings.voice_id or get_default_voice_id(self._sync_client)

        self._capture_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._text_queue: queue.Queue[str | object] = queue.Queue(maxsize=50)
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

        self._segments_processed = 0
        self._errors = 0
        self._start_time = 0.0

    def start(self):
        """Start the fast pipeline. Blocks until Ctrl+C."""
        logger.info("Starting fast pipeline (STT→TTS, voice_id=%s)", self.voice_id)
        print(f"\nFast voice changer active! Voice: {self.voice_id}")
        print("Mode: STT → TTS (lower latency, AI-generated delivery)")

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

        # Start TTS thread
        tts_thread = threading.Thread(target=self._tts_loop, daemon=True)
        tts_thread.start()

        # Run STT in asyncio event loop (blocks main thread)
        try:
            asyncio.run(self._stt_loop())
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            self.stop()

    async def _stt_loop(self):
        """Async loop: capture mic frames → send to STT WebSocket → text queue."""
        async_client = AsyncElevenLabs(api_key=self.settings.api_key)

        try:
            connection = await async_client.speech_to_text.realtime.connect(
                {
                    "model_id": "scribe_v2_realtime",
                    "audio_format": AudioFormat.PCM_16000,
                    "sample_rate": self.settings.sample_rate,
                    "commit_strategy": CommitStrategy.VAD,
                    "language_code": "en",
                }
            )
        except Exception as e:
            logger.error("Failed to connect STT WebSocket: %s", e)
            self._stop_event.set()
            return

        logger.info("STT WebSocket connected")

        # Register transcript handler (must be sync, not async)
        def _on_transcript(data):
            text = data.get("text", "").strip()
            if text:
                logger.info("STT transcript: %s", text)
                self._text_queue.put(text)

        def _on_error(data):
            logger.error("STT error: %s", data)

        connection.on("committed_transcript", _on_transcript)
        connection.on("error", _on_error)

        ptt_was_active = False

        try:
            while not self._stop_event.is_set():
                try:
                    frame = self._capture_queue.get(timeout=0.1)
                except queue.Empty:
                    # Check PTT release
                    if self.ptt and ptt_was_active and not self.ptt.is_active:
                        ptt_was_active = False
                    continue

                # PTT gating
                if self.ptt:
                    if not self.ptt.is_active:
                        if ptt_was_active:
                            ptt_was_active = False
                        continue
                    ptt_was_active = True

                # Feedback suppression (auto mode)
                if not self.ptt and self._playing_event.is_set():
                    continue

                # Send frame to STT
                audio_b64 = base64.b64encode(frame).decode("utf-8")
                try:
                    await connection.send({"audio_base_64": audio_b64})
                except Exception as e:
                    logger.error("STT send error: %s", e)
                    break

        finally:
            try:
                await connection.close()
            except Exception:
                pass
            logger.info("STT WebSocket closed")

    def _tts_loop(self):
        """Sync loop: read text from queue → TTS WebSocket → audio to output queue."""
        logger.info("TTS thread started")

        while not self._stop_event.is_set():
            try:
                text = self._text_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if text is _SENTINEL:
                break

            start = time.monotonic()
            first_chunk = True

            try:
                # Use sync TTS WebSocket
                from elevenlabs import VoiceSettings
                audio_iter = self._sync_client.text_to_speech.convert_realtime(
                    voice_id=self.voice_id,
                    text=iter([text]),
                    model_id="eleven_flash_v2_5",
                    output_format="pcm_16000",
                    voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75),
                )

                for chunk in audio_iter:
                    if first_chunk:
                        latency = time.monotonic() - start
                        logger.info("TTS first-byte latency: %.0fms", latency * 1000)
                        first_chunk = False
                    self._output_queue.put(chunk)

                self._segments_processed += 1

            except Exception as e:
                logger.error("TTS error: %s", e)
                self._errors += 1

        logger.info("TTS thread stopped")

    def stop(self):
        """Stop all components."""
        self._stop_event.set()
        self._text_queue.put(_SENTINEL)

        if self.ptt:
            self.ptt.stop()
        self._capture.stop()
        self._output_queue.put(None)
        self._playback.stop()

        elapsed = time.monotonic() - self._start_time
        print(f"\nSession stats (fast mode):")
        print(f"  Duration: {elapsed:.1f}s")
        print(f"  Segments processed: {self._segments_processed}")
        print(f"  Errors: {self._errors}")


def run_fast_test(
    settings: Settings,
    input_file: str,
    output_file: str = "output.wav",
) -> Path:
    """File-based test for fast mode: WAV → STT → text → TTS → WAV."""
    input_path = Path(input_file)
    output_path = Path(output_file)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Read input WAV
    with wave.open(str(input_path), "rb") as wf:
        assert wf.getnchannels() == 1, f"Expected mono, got {wf.getnchannels()} channels"
        assert wf.getsampwidth() == 2, f"Expected 16-bit, got {wf.getsampwidth() * 8}-bit"
        input_rate = wf.getframerate()
        pcm_data = wf.readframes(wf.getnframes())

    input_duration = len(pcm_data) / (input_rate * 2)
    logger.info("Input: %s (%.1fs, %dHz)", input_path.name, input_duration, input_rate)

    # Resample if needed
    if input_rate != settings.sample_rate:
        from voice_changer.pipeline import _resample_pcm
        pcm_data = _resample_pcm(pcm_data, input_rate, settings.sample_rate)

    # Step 1: STT — transcribe the audio
    print("Step 1/2: Transcribing audio (STT)...")
    transcript = asyncio.run(_transcribe_audio(settings, pcm_data))
    if not transcript:
        raise RuntimeError("STT produced no transcript")
    logger.info("Transcript: %s", transcript)
    print(f"  Transcript: \"{transcript}\"")

    # Step 2: TTS — synthesize in target voice
    print("Step 2/2: Synthesizing in target voice (TTS)...")
    sync_client = ElevenLabs(api_key=settings.api_key)
    voice_id = settings.voice_id or get_default_voice_id(sync_client)

    start = time.monotonic()
    all_output = bytearray()
    first_chunk = True

    from elevenlabs import VoiceSettings
    audio_iter = sync_client.text_to_speech.convert_realtime(
        voice_id=voice_id,
        text=iter([transcript]),
        model_id="eleven_flash_v2_5",
        output_format="pcm_16000",
        voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75),
    )

    for chunk in audio_iter:
        if first_chunk:
            latency = time.monotonic() - start
            logger.info("TTS first-byte latency: %.0fms", latency * 1000)
            first_chunk = False
        all_output.extend(chunk)

    if not all_output:
        raise RuntimeError("TTS produced no audio output")

    # Write output WAV
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(settings.sample_rate)
        wf.writeframes(bytes(all_output))

    out_duration = len(all_output) / (settings.sample_rate * 2)
    print(f"\nFast mode test complete!")
    print(f"  Input:  {input_path} ({input_duration:.1f}s)")
    print(f"  Output: {output_path} ({out_duration:.1f}s)")
    print(f"  Transcript: \"{transcript}\"")

    return output_path


async def _transcribe_audio(settings: Settings, pcm_data: bytes) -> str:
    """Transcribe PCM audio using Scribe v2 Realtime WebSocket."""
    async_client = AsyncElevenLabs(api_key=settings.api_key)
    transcripts: list[str] = []
    done_event = asyncio.Event()

    connection = await async_client.speech_to_text.realtime.connect(
        {
            "model_id": "scribe_v2_realtime",
            "audio_format": AudioFormat.PCM_16000,
            "sample_rate": settings.sample_rate,
            "commit_strategy": CommitStrategy.MANUAL,
            "language_code": "en",
        }
    )

    def _on_transcript(data):
        text = data.get("text", "").strip()
        if text:
            transcripts.append(text)
            logger.info("STT chunk: %s", text)
        done_event.set()

    def _on_error(data):
        logger.error("STT error: %s", data)
        done_event.set()

    connection.on("committed_transcript", _on_transcript)
    connection.on("error", _on_error)

    # Send audio in chunks (with pacing to simulate real-time)
    frame_bytes = settings.frame_bytes
    frame_duration = settings.chunk_duration_ms / 1000.0
    start = time.monotonic()
    chunk_count = 0

    for offset in range(0, len(pcm_data), frame_bytes):
        chunk = pcm_data[offset : offset + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
        audio_b64 = base64.b64encode(chunk).decode("utf-8")
        await connection.send({"audio_base_64": audio_b64})
        chunk_count += 1
        # Pace at ~4x real-time (fast enough but gives server time to process)
        if chunk_count % 4 == 0:
            await asyncio.sleep(frame_duration)

    # Commit and wait for transcript
    await connection.commit()

    latency = time.monotonic() - start
    logger.info("STT audio sent in %.0fms (%d chunks)", latency * 1000, chunk_count)

    # Wait for transcript with timeout
    try:
        await asyncio.wait_for(done_event.wait(), timeout=15.0)
    except asyncio.TimeoutError:
        logger.warning("STT transcript timeout")

    await connection.close()

    return " ".join(transcripts)
