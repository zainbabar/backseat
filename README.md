# backseat

A sarcastic friend watching over your shoulder while you use your computer.
Select an area of your screen; Backseat occasionally observes it, writes a short
remark, and speaks it using your ElevenLabs voice.

This is a macOS Python prototype. It uses GPT-5.4 mini for vision/commentary and
ElevenLabs Flash v2.5 for speech. It gives commentary only, without coding hints
or LeetCode spoilers. Nothing launches automatically or runs after you quit.

## Setup

```sh
uv sync
cp .env.example .env
```

Fill `.env` with `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, and your
`ELEVENLABS_VOICE_ID`. No default voice is substituted. Only the OpenAI key is
needed for `--text-only`; preview needs no keys.

Enable your launching terminal/app under **System Settings → Privacy & Security
→ Screen Recording**, then quit and reopen that app. Backseat checks this before
capturing. Depending on your macOS version, the setting may be named **Screen &
System Audio Recording**. Backseat does not record audio or use a microphone.

## Four runnable milestones

```sh
# 1. Select a display, drag a rectangle, confirm the preview; no network calls.
uv run backseat preview

# 2. One screenshot → printed observation-based remark (or silence).
uv run backseat once --text-only

# 3. One screenshot → spoken remark using your voice.
uv run backseat once

# 4. Automatic companion with recent context and comfortable gaps.
uv run backseat run
```

Use `--monitor 2` to skip the display prompt. Escape cancels selection; the preview
lets you retry or cancel before any API calls. On an ultrawide, select the editor
and/or problem statement rather than the whole display. Fractional crop coordinates
handle logical/physical display scaling; the preview shows the resulting pixel size.
Screenshots sent to the model are resized proportionally to a maximum edge of 2560
pixels by default. The preview uses the same resizing as the model input, so check
readability there.

In `run`, type a command and press Enter:

- `pause` stops playback and new captures/API calls; pending output is discarded.
- `resume` starts watching again.
- `region` pauses while you select another rectangle on the current display; use
  `region 2` to change displays. It resets recent history,
  and returns to your previous paused/running state. Cancel keeps the existing region.
- `stats` shows requests, tokens, speech characters, and average model latency.
- `quit` or Ctrl+C stops playback and exits.

An already submitted HTTP request cannot be recalled. Shutdown waits for it to
finish or time out; its output will never be played after pause/quit.

## Timing and personality

Edit `backseat.toml` to change model IDs, sampling, cooldown, image size, and timeouts.
The checked-in configuration currently uses faster testing timing: checks every
15 seconds when eligible and at least 15 seconds after speech ends before another
remark. Restore `sample_seconds = 30` and `cooldown_seconds = 60` for quieter use.
The loop skips essentially unchanged screens.
It does not force a joke every minute; the model may choose silence. Small visual
changes accumulate against the last submitted screenshot.

The prompt lives in `src/backseat/providers.py`. Recent history contains at most
six timestamped observations and delivered remarks, allowing grounded callbacks.
The narrator can swear, but should avoid generic roasts, invented events, unsolicited
advice, and repeated punchlines. Prompt instructions reduce mistakes; they do not
guarantee factual or spoiler-free output. Tune with `--text-only` first.

## Data and usage

Only the chosen crop is sent to OpenAI. ElevenLabs receives the generated remark.
Screenshots and history stay in local memory unless you explicitly use
`--save-captures captures`. That flag saves submitted crops (or the confirmed preview)
for debugging. Captures and `.env` are ignored by git. OpenAI requests use
`store=false`; provider retention policies still apply.

No conversation database, telemetry service, or automatic cost estimate is included.
Session statistics show actual reported token use and requested speech characters,
including requests whose output you later discard. Failed requests may not return
token usage. Check provider dashboards for billing and configure provider spending
limits there. Model silence still costs a vision request, but no speech request.

## Verification

```sh
uv run pytest
uv run ruff check .
```

Tests use fake providers and images; they do not capture your desktop or spend credits.
Before calling the prototype validated end to end, manually check the G9 crop,
readable code, failed/successful test runs, browser switching, idle-screen silence,
pause during generation and audio, and your specific voice. Run a 15-minute session
to measure real latency, usage, and whether the timing feels right.
