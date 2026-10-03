import argparse
import os
import queue
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from .capture import CaptureError, check_permission, monitors, select_region
from .core import Config, Stats
from .providers import Providers
from .runtime import Runner


def parser():
    result = argparse.ArgumentParser(description="A sarcastic friend watching your desktop.")
    result.add_argument("command", choices=["preview", "once", "run"])
    result.add_argument("--monitor", type=int, help="Display number (1-based); prompts if omitted")
    result.add_argument("--config", type=Path, default=Path("backseat.toml"))
    result.add_argument("--text-only", action="store_true", help="Skip ElevenLabs and audio")
    result.add_argument(
        "--save-captures",
        type=Path,
        metavar="DIRECTORY",
        help="Explicitly save submitted crops for debugging",
    )
    return result


def choose_monitor(number):
    displays = monitors()
    if not displays:
        raise CaptureError("No displays found.")
    if number is None:
        for index, display in enumerate(displays, 1):
            print(
                f"{index}: {display['width']}×{display['height']} "
                f"at ({display['left']}, {display['top']})"
            )
        number = int(input("Display to watch [1]: ").strip() or "1")
    if number < 1 or number > len(displays):
        raise ValueError(f"Display must be between 1 and {len(displays)}.")
    return displays[number - 1]


def read_commands(commands):
    for line in sys.stdin:
        commands.put(line.strip().lower())
    commands.put("quit")


def credentials(text_only):
    names = ["OPENAI_API_KEY"]
    if not text_only:
        names += ["ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"]
    missing = [name for name in names if not os.getenv(name, "").strip()]
    if missing:
        raise ValueError("Missing " + ", ".join(missing) + ". Fill .env using .env.example.")
    return dict(
        openai_key=os.environ["OPENAI_API_KEY"],
        elevenlabs_key="" if text_only else os.environ["ELEVENLABS_API_KEY"],
        voice_id="" if text_only else os.environ["ELEVENLABS_VOICE_ID"],
    )


def main():
    args = parser().parse_args()
    runner = None
    stats = Stats()
    try:
        config = Config.load(args.config)
        load_dotenv(Path(".env"))
        keys = credentials(args.text_only) if args.command != "preview" else None
        check_permission()
        region = select_region(choose_monitor(args.monitor), config.image_max_edge)
        if region is None:
            print("Canceled. No API calls made.")
            return 0
        if args.command == "preview":
            if args.save_captures:
                args.save_captures.mkdir(parents=True, exist_ok=True)
                region.capture().save(args.save_captures / "preview.png")
            print("Capture preview confirmed. No API calls made.")
            return 0
        providers = Providers(config, stats, **keys)
        runner = Runner(
            config, providers, region, text_only=args.text_only, capture_dir=args.save_captures
        )
        commands = queue.Queue()
        if args.command == "run":
            print(
                "Watching selected area. Commands: pause, resume, region, stats, quit (then Enter)."
            )
            threading.Thread(target=read_commands, args=(commands,), daemon=True).start()
        submitted = False
        while True:
            try:
                command = commands.get_nowait()
            except queue.Empty:
                command = None
            if command == "quit":
                break
            if command == "pause":
                runner.pause()
            elif command == "resume":
                runner.resume()
            elif command == "stats":
                print(stats.summary())
            elif command and command.split()[0] == "region":
                was_paused = runner.session.paused
                runner.pause()
                parts = command.split()
                try:
                    monitor = (
                        choose_monitor(int(parts[1])) if len(parts) == 2 else runner.region.monitor
                    )
                    if len(parts) > 2:
                        raise ValueError("Use 'region' or 'region DISPLAY_NUMBER'.")
                except ValueError as exc:
                    print(exc)
                    if not was_paused:
                        runner.resume()
                    continue
                selected = select_region(monitor, config.image_max_edge)
                if selected is not None:
                    runner.reselect(selected)
                if not was_paused:
                    runner.resume()
            elif command:
                print("Commands: pause, resume, region, stats, quit")
            runner.tick()
            submitted = submitted or runner.pending is not None
            if args.command == "once" and (
                runner.had_error
                or runner.session.paused
                or (submitted and runner.pending is None and runner.playing_entry is None)
            ):
                break
            time.sleep(0.1)
        return int(args.command == "once" and runner.had_error)
    except KeyboardInterrupt:
        print("\nStopping Backseat.")
        return 0
    except (CaptureError, ValueError, EOFError) as exc:
        print(f"Backseat: {exc}", file=sys.stderr)
        return 1
    finally:
        if runner:
            runner.close()
            print(stats.summary())


if __name__ == "__main__":
    raise SystemExit(main())
