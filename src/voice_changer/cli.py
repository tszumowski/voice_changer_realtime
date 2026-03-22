"""CLI entry point for the voice changer."""

from __future__ import annotations

import argparse
import logging
import sys


def _add_mode_arg(parser):
    """Add --mode argument to a subparser."""
    parser.add_argument(
        "--mode", default="normal", choices=["normal", "fast", "resemble"],
        help="Voice changing mode: normal (ElevenLabs STS, ~1s), "
             "fast (STT→TTS WebSocket, ~300ms, loses prosody), "
             "resemble (Resemble.ai STS, preserves prosody)"
    )


def main():
    parser = argparse.ArgumentParser(
        prog="voice-changer",
        description="Near-real-time voice changer using ElevenLabs STS + BlackHole",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # list-devices
    p_devices = sub.add_parser("list-devices", help="List available audio devices")
    p_devices.add_argument("--input-only", action="store_true")
    p_devices.add_argument("--output-only", action="store_true")

    # list-voices
    p_voices = sub.add_parser("list-voices", help="List available voices")
    p_voices.add_argument(
        "--provider", default="elevenlabs", choices=["elevenlabs", "resemble"],
        help="Voice provider (default: elevenlabs)"
    )

    # test
    p_test = sub.add_parser("test", help="File-based E2E test (no mic needed)")
    p_test.add_argument("-i", "--input-file", required=True, help="Input WAV file")
    p_test.add_argument("-o", "--output-file", default="output.wav", help="Output WAV file")
    p_test.add_argument("--voice-id", help="Target voice ID (ElevenLabs or Resemble UUID)")
    p_test.add_argument("--model", default="eleven_english_sts_v2", help="STS model ID")
    _add_mode_arg(p_test)

    # live
    p_live = sub.add_parser("live", help="Start live voice changing")
    p_live.add_argument("--voice-id", help="Target voice ID (ElevenLabs or Resemble UUID)")
    p_live.add_argument("--input-device", type=int, help="Input device index")
    p_live.add_argument("--output-device", type=int, help="Output device index")
    p_live.add_argument("--model", default="eleven_english_sts_v2", help="STS model ID")
    p_live.add_argument(
        "--segment-duration", type=float, default=1.0, help="Max speech segment (seconds)"
    )
    p_live.add_argument(
        "--vad-aggressiveness", type=int, default=2, choices=[1, 2, 3],
        help="VAD aggressiveness (1=least, 3=most)"
    )
    p_live.add_argument(
        "--ptt", nargs="?", const="right_cmd", default=None, metavar="KEY",
        help="Enable push-to-talk mode. Hold KEY to record, release to send. "
             "Default key: right_cmd. Options: space, right_cmd, right_ctrl, f1-f20, "
             "or any single character."
    )
    _add_mode_arg(p_live)

    args = parser.parse_args()

    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=level,
    )

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "list-devices":
        from voice_changer.audio_devices import print_devices
        direction = None
        if args.input_only:
            direction = "input"
        elif args.output_only:
            direction = "output"
        print_devices(direction)

    elif args.command == "list-voices":
        if args.provider == "resemble":
            from voice_changer.config import load_settings
            from voice_changer.resemble_pipeline import list_resemble_voices

            settings = load_settings()
            if not settings.resemble_api_key:
                print("Error: RESEMBLE_API_KEY not set in .env")
                sys.exit(1)
            voices = list_resemble_voices(settings.resemble_api_key)
            print(f"\n{'Voice UUID':<40} {'Name':<20} {'STS Status'}")
            print("-" * 70)
            for v in voices:
                print(f"{v['voice_uuid']:<40} {v['name']:<20} {v['status']}")
            print(f"\n{len(voices)} voice(s) available.\n")
        else:
            from voice_changer.config import load_settings
            from voice_changer.transformer import create_client, list_voices

            settings = load_settings()
            client = create_client(settings.api_key)
            voices = list_voices(client)
            print(f"\n{'Voice ID':<25} {'Name':<20} {'Category'}")
            print("-" * 60)
            for v in voices:
                print(f"{v['voice_id']:<25} {v['name']:<20} {v['category']}")
            print(f"\n{len(voices)} voice(s) available.\n")

    elif args.command == "test":
        from voice_changer.config import load_settings

        mode = args.mode

        if mode == "fast":
            from voice_changer.fast_pipeline import run_fast_test
            settings = load_settings(voice_id=args.voice_id, mode=mode)
            run_fast_test(settings, args.input_file, args.output_file)

        elif mode == "resemble":
            from voice_changer.resemble_pipeline import run_resemble_test
            settings = load_settings(
                voice_id=args.voice_id,
                mode=mode,
                resemble_voice_uuid=args.voice_id,
            )
            run_resemble_test(settings, args.input_file, args.output_file)

        else:  # normal
            from voice_changer.pipeline import run_test
            settings = load_settings(voice_id=args.voice_id, model_id=args.model)
            run_test(settings, args.input_file, args.output_file)

    elif args.command == "live":
        from voice_changer.config import load_settings

        mode = args.mode

        # Build PTT if requested
        ptt = None
        if args.ptt is not None:
            from voice_changer.ptt import PushToTalk, parse_ptt_key
            key = parse_ptt_key(args.ptt)
            ptt = PushToTalk(key=key)

        base_kwargs = dict(
            voice_id=args.voice_id,
            input_device=args.input_device,
            output_device=args.output_device,
            segment_duration_s=args.segment_duration,
            vad_aggressiveness=args.vad_aggressiveness,
            mode=mode,
        )

        if mode == "fast":
            from voice_changer.fast_pipeline import FastPipeline
            settings = load_settings(**base_kwargs)
            pipeline = FastPipeline(settings, ptt=ptt)

        elif mode == "resemble":
            from voice_changer.resemble_pipeline import ResemblePipeline
            # Use longer segments for Resemble (sync API, not streaming)
            if args.segment_duration == 1.0:  # default wasn't overridden
                base_kwargs["segment_duration_s"] = 3.0
            settings = load_settings(
                resemble_voice_uuid=args.voice_id,
                **base_kwargs,
            )
            pipeline = ResemblePipeline(settings, ptt=ptt)

        else:  # normal
            from voice_changer.pipeline import LivePipeline
            settings = load_settings(model_id=args.model, **base_kwargs)
            pipeline = LivePipeline(settings, ptt=ptt)

        pipeline.start()
