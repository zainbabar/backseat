import argparse
import json
import math
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import get_args

from dotenv import load_dotenv

from .captions import Captions
from .capture import CaptureError, check_permission, monitors, select_region
from .core import Config, Persona, Stats, Task, timestamp
from .memory import MemoryStore, MemoryStoreError
from .providers import Providers
from .runtime import Runner


def interval_seconds(value):
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be a number of seconds") from exc
    if not math.isfinite(seconds) or seconds < 1:
        raise argparse.ArgumentTypeError("interval must be a finite number of at least 1 second")
    return seconds


def parser():
    result = argparse.ArgumentParser(description="A sarcastic friend watching your desktop.")
    result.add_argument("command", choices=["preview", "once", "run"])
    result.add_argument("--monitor", type=int, help="Display number (1-based); prompts if omitted")
    result.add_argument("--config", type=Path, default=Path("backseat.toml"))
    result.add_argument(
        "--interval",
        type=interval_seconds,
        metavar="SECONDS",
        help="Override observation interval and speech cooldown for this run (minimum 1 second)",
    )
    result.add_argument("--text-only", action="store_true", help="Print remarks; skip all audio")
    result.add_argument(
        "--voice",
        choices=["auto", "elevenlabs", "say"],
        default="auto",
        help="Speech engine. auto uses ElevenLabs when its keys are set, else the macOS voice",
    )
    result.add_argument(
        "--persona",
        choices=get_args(Persona),
        help="Commentary style for this run (default: persona in config, else friend)",
    )
    result.add_argument(
        "--captions",
        action="store_true",
        help="Show each remark as a subtitle beside (never over) the watched region",
    )
    result.add_argument(
        "--recap", action="store_true", help="Deliver a short session recap on quit (run only)"
    )
    result.add_argument(
        "--chatty",
        action="store_true",
        help="Testing: request a remark every interval, including on unchanged screens",
    )
    result.add_argument(
        "--force-remark",
        action="store_true",
        help="Testing: force a remark on the first capture (also works with once)",
    )
    result.add_argument(
        "--save-captures",
        type=Path,
        metavar="DIRECTORY",
        help="Explicitly save submitted crops for debugging",
    )
    start = result.add_mutually_exclusive_group()
    start.add_argument("--resume", action="store_true", help="Resume last saved task (run only)")
    start.add_argument(
        "--fresh", action="store_true", help="Start fresh, keeping humor preferences"
    )
    result.add_argument("--task", help="Goal for a new task")
    result.add_argument(
        "--observe-only", action="store_true", help="Track context without writer/audio"
    )
    return result


def choose_task(store, args, *, interactive):
    if args.command != "run":
        return Task(goal=args.task or "desktop session")
    if not interactive and not (args.resume or args.fresh):
        raise ValueError("Noninteractive run requires --resume or --fresh.")
    if args.resume and args.task:
        raise ValueError(
            "Use --fresh --task TEXT to start a named task, or --resume without --task."
        )
    saved = store.last_task()
    resume = args.resume
    if interactive and not (args.resume or args.fresh):
        while True:
            suffix = "" if saved else " (no saved task yet)"
            choice = (
                input(f"Resume last task or start fresh{suffix}? [resume/fresh]: ").strip().lower()
            )
            if choice in ("resume", "fresh"):
                resume = choice == "resume"
                break
            print("Enter resume or fresh.")
    if resume and saved:
        print("Resuming saved task. Old events retain their original timestamps.")
        return saved
    if resume:
        print("No saved task; starting fresh.")
    return Task(goal=args.task or "desktop session")


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
        answer = input("Display to watch [1]: ").strip() or "1"
        number = int(answer) if answer.isdigit() else 0
    if number < 1 or number > len(displays):
        raise ValueError(f"Display must be between 1 and {len(displays)}.")
    return displays[number - 1]


def read_commands(commands):
    for line in sys.stdin:
        commands.put(line.strip())
    commands.put("quit")


ELEVENLABS = ["ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"]


def speech_engine(silent, voice):
    if silent:
        return None
    if voice == "auto":
        return "elevenlabs" if all(os.getenv(name, "").strip() for name in ELEVENLABS) else "say"
    return voice


def credentials(engine):
    names = ["OPENAI_API_KEY"] + (ELEVENLABS if engine == "elevenlabs" else [])
    missing = [name for name in names if not os.getenv(name, "").strip()]
    if missing:
        raise ValueError("Missing " + ", ".join(missing) + ". Fill .env using .env.example.")
    eleven = engine == "elevenlabs"
    return dict(
        openai_key=os.environ["OPENAI_API_KEY"],
        elevenlabs_key=os.environ["ELEVENLABS_API_KEY"] if eleven else "",
        voice_id=os.environ["ELEVENLABS_VOICE_ID"] if eleven else "",
    )


def main():
    args = parser().parse_args()
    runner = None
    memory = None
    captions = None
    stats = Stats()
    try:
        if args.command != "run" and (args.resume or args.fresh):
            raise ValueError("--resume/--fresh apply to run; once is always isolated.")
        if args.task is not None:
            args.task = args.task.strip()
            if not args.task or len(args.task) > 2000:
                raise ValueError("--task requires 1–2000 characters.")
        if args.resume and args.task:
            raise ValueError("Use --fresh --task TEXT for a new named task, or --resume alone.")
        if args.command == "run" and not sys.stdin.isatty() and not (args.resume or args.fresh):
            raise ValueError("Noninteractive run requires --resume or --fresh.")
        if args.recap and args.command != "run":
            raise ValueError("--recap applies to run.")
        if args.observe_only and (args.recap or args.captions):
            raise ValueError("--recap and --captions need the writer; drop --observe-only.")
        config = Config.load(args.config)
        overrides = {}
        if args.interval is not None:
            overrides.update(sample_seconds=args.interval, cooldown_seconds=args.interval)
        if args.persona is not None:
            overrides["persona"] = args.persona
        if overrides:
            config = Config.model_validate({**config.model_dump(), **overrides})
        load_dotenv(Path(".env"))
        engine = speech_engine(args.text_only or args.observe_only, args.voice)
        keys = credentials(engine) if args.command != "preview" else None
        if engine == "say" and args.command != "preview":
            print("Voice: built-in macOS say. Set ElevenLabs keys in .env for a custom voice.")
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
        if args.captions:
            captions = Captions()
        memory = MemoryStore(readonly=args.command == "once")
        task = choose_task(memory, args, interactive=sys.stdin.isatty())
        preferences = memory.preferences()
        task.updated = timestamp()
        memory.save(task, preferences)
        providers = Providers(config, stats, **keys)
        runner = Runner(
            config,
            providers,
            region,
            task=task,
            preferences=preferences,
            memory=memory,
            text_only=args.text_only,
            observe_only=args.observe_only,
            once=args.command == "once",
            chatty=args.chatty,
            force_remark=args.force_remark,
            capture_dir=args.save_captures,
            captions=captions,
        )
        commands = queue.Queue()
        if args.command == "run":
            print(
                "Watching. Commands: pause, resume, region [N], task TEXT, context TEXT, "
                "taste TEXT, memory, funny, boring, correct TEXT, stats, quit (then Enter)."
            )
            threading.Thread(target=read_commands, args=(commands,), daemon=True).start()
        while True:
            try:
                command = commands.get_nowait()
            except queue.Empty:
                command = None
            verb, _, text = (command or "").partition(" ")
            verb = verb.lower()
            text = text.strip()
            if verb == "quit":
                if args.recap:
                    runner.recap()
                break
            if verb == "pause":
                runner.pause()
            elif verb == "resume":
                runner.resume()
            elif verb == "stats":
                print(stats.summary())
            elif verb == "region":
                was_paused = runner.session.paused
                runner.pause()
                parts = command.split()
                try:
                    if len(parts) > 2 or (len(parts) == 2 and not parts[1].isdigit()):
                        raise ValueError("Use 'region' or 'region DISPLAY_NUMBER'.")
                    monitor = (
                        choose_monitor(int(parts[1])) if len(parts) == 2 else runner.region.monitor
                    )
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
            elif verb in ("task", "context", "taste", "correct"):
                if not text or len(text) > 2000:
                    print(f"Use {verb} TEXT (1–2000 characters).")
                else:
                    try:
                        if verb == "task":
                            runner.new_task(text)
                        elif verb == "taste":
                            runner.taste(text)
                        else:
                            runner.add_context(text, correction=verb == "correct")
                    except MemoryStoreError as exc:
                        runner.fail(exc, "memory")
            elif verb in ("funny", "boring"):
                try:
                    runner.feedback(verb)
                except MemoryStoreError as exc:
                    runner.fail(exc, "memory")
            elif verb == "memory":
                # User explicitly requested this local context inspection.
                print(
                    json.dumps(
                        {
                            "task": runner.session.task.context(),
                            "taste": runner.session.preferences,
                        },
                        indent=2,
                    )
                )
            elif command:
                print(
                    "Commands: pause, resume, region [N], task TEXT, context TEXT, taste TEXT, "
                    "memory, funny, boring, correct TEXT, stats, quit"
                )
            runner.tick()
            if args.command == "once" and runner.finished:
                break
            time.sleep(0.1)
        return int(args.command == "once" and runner.had_error)
    except KeyboardInterrupt:
        print("\nStopping Backseat.")
        return 0
    except (CaptureError, MemoryStoreError, ValueError, EOFError) as exc:
        print(f"Backseat: {exc}", file=sys.stderr)
        return 1
    finally:
        if runner:
            runner.close()
            print(stats.summary())
        if captions:
            captions.close()
        if memory:
            memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
