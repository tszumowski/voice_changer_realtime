"""Pipeline orchestrator: wires capture -> VAD -> transform -> playback."""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from voice_changer.capture import MicCapture
from voice_changer.config import Settings
from voice_changer.playback import AudioPlayback
from voice_changer.transformer import (
    create_client,
    get_default_voice_id,
    transform_segment,
)
from voice_changer.vad import VoiceActivityDetector

logger = logging.getLogger(__name__)


class LivePipeline:
    """Real-time voice changing pipeline: mic -> VAD -> ElevenLabs -> speaker."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = create_client(settings.api_key)
        self.voice_id = settings.voice_id or get_default_voice_id(self.client)

        self._capture_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._output_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=200)
        self._stop_event = threading.Event()

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
        """Start the live pipeline. Blocks until Ctrl+C."""
        logger.info("Starting live pipeline (voice_id=%s)", self.voice_id)
        print(f"\nVoice changer active! Voice: {self.voice_id}")
        print("Speak into your microphone. Press Ctrl+C to stop.\n")

        self._start_time = time.monotonic()
        self._capture.start()
        self._playback.start()

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                self._process_loop(executor)
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            self.stop()

    def _process_loop(self, executor: ThreadPoolExecutor):
        """Main loop: read frames from capture, run VAD, transform segments."""
        while not self._stop_event.is_set():
            try:
                frame = self._capture_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            segment = self._vad.process_frame(frame)
            if segment is not None:
                # Submit transform to thread pool (non-blocking)
                executor.submit(self._transform_and_play, segment)

    def _transform_and_play(self, segment: bytes):
        """Transform a segment and push to playback queue."""
        try:
            for chunk in transform_segment(
                client=self.client,
                pcm_audio=segment,
                voice_id=self.voice_id,
                model_id=self.settings.model_id,
                output_format=self.settings.output_format,
                remove_background_noise=self.settings.remove_background_noise,
                sample_rate=self.settings.sample_rate,
            ):
                if self._stop_event.is_set():
                    break
                self._output_queue.put(chunk)
            self._segments_processed += 1
        except Exception as e:
            logger.error("Transform error: %s", e)
            self._errors += 1

    def stop(self):
        """Stop all components and print stats."""
        self._stop_event.set()
        # Flush remaining VAD buffer
        remaining = self._vad.flush()
        if remaining:
            logger.debug("Flushing final VAD segment")

        self._capture.stop()
        self._output_queue.put(None)  # Poison pill for playback
        self._playback.stop()

        elapsed = time.monotonic() - self._start_time
        print(f"\nSession stats:")
        print(f"  Duration: {elapsed:.1f}s")
        print(f"  Segments processed: {self._segments_processed}")
        print(f"  Errors: {self._errors}")


def run_test(
    settings: Settings,
    input_file: str,
    output_file: str = "output.wav",
) -> Path:
    """File-based test: read WAV -> VAD segments -> transform -> write WAV.

    Returns the output file path.
    """
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

    logger.info(
        "Input: %s (%.1fs, %dHz, %d bytes)",
        input_path.name,
        len(pcm_data) / (input_rate * 2),
        input_rate,
        len(pcm_data),
    )

    # If sample rate doesn't match, we need to resample
    if input_rate != settings.sample_rate:
        logger.info("Resampling from %d to %d Hz", input_rate, settings.sample_rate)
        pcm_data = _resample_pcm(pcm_data, input_rate, settings.sample_rate)

    # Run VAD to extract speech segments
    vad = VoiceActivityDetector(
        sample_rate=settings.sample_rate,
        frame_duration_ms=settings.chunk_duration_ms,
        aggressiveness=settings.vad_aggressiveness,
        silence_end_frames=settings.silence_frames,
        max_segment_frames=settings.max_segment_frames,
    )

    frame_bytes = settings.frame_bytes
    segments = []
    offset = 0

    while offset + frame_bytes <= len(pcm_data):
        frame = pcm_data[offset : offset + frame_bytes]
        segment = vad.process_frame(frame)
        if segment is not None:
            segments.append(segment)
        offset += frame_bytes

    # Flush remaining
    remaining = vad.flush()
    if remaining:
        segments.append(remaining)

    if not segments:
        # No speech detected - send the whole file as one segment
        logger.warning("No speech segments detected by VAD, sending entire file")
        segments = [pcm_data]

    logger.info("Extracted %d speech segment(s)", len(segments))

    # Set up ElevenLabs client
    client = create_client(settings.api_key)
    voice_id = settings.voice_id or get_default_voice_id(client)
    logger.info("Using voice: %s", voice_id)

    # Transform each segment and collect output
    all_output = bytearray()
    for i, segment in enumerate(segments):
        seg_duration = len(segment) / (settings.sample_rate * 2)
        logger.info("Transforming segment %d/%d (%.1fs)...", i + 1, len(segments), seg_duration)

        for chunk in transform_segment(
            client=client,
            pcm_audio=segment,
            voice_id=voice_id,
            model_id=settings.model_id,
            output_format=settings.output_format,
            remove_background_noise=settings.remove_background_noise,
            sample_rate=settings.sample_rate,
        ):
            all_output.extend(chunk)

    if not all_output:
        raise RuntimeError("No audio output received from API")

    # Write output WAV
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(settings.sample_rate)
        wf.writeframes(bytes(all_output))

    out_duration = len(all_output) / (settings.sample_rate * 2)
    logger.info("Output: %s (%.1fs, %d bytes)", output_path.name, out_duration, len(all_output))
    print(f"\nTest complete!")
    print(f"  Input:  {input_path} ({len(pcm_data) / (settings.sample_rate * 2):.1f}s)")
    print(f"  Output: {output_path} ({out_duration:.1f}s)")
    print(f"  Segments: {len(segments)}")

    return output_path


def _resample_pcm(pcm_data: bytes, from_rate: int, to_rate: int) -> bytes:
    """Simple linear resampling of 16-bit PCM data."""
    import numpy as np

    samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float64)
    ratio = to_rate / from_rate
    new_length = int(len(samples) * ratio)
    indices = np.linspace(0, len(samples) - 1, new_length)
    resampled = np.interp(indices, np.arange(len(samples)), samples)
    return resampled.astype(np.int16).tobytes()
