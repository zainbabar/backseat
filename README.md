# backseat

A sarcastic friend watching over your shoulder while you use your computer.
Select an area of your screen; Backseat follows what you're doing and occasionally
speaks a short remark using your ElevenLabs voice.

This is a macOS Python prototype. It gives commentary only, without coding hints
or LeetCode spoilers. Nothing launches automatically or runs after you quit.

## Setup

```sh
uv sync
# If you don't already have .env:
cp .env.example .env
```

Fill `.env` with `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, and your
`ELEVENLABS_VOICE_ID`. No default voice is substituted. Only the OpenAI key is
needed for `--text-only` or `--observe-only`; preview needs no keys.

Enable your launching terminal/app (e.g. Ghostty) under **System Settings → Privacy
& Security → Screen Recording**, then quit and reopen it. On some macOS versions
this is named **Screen & System Audio Recording**. Backseat checks permission before
capturing; it does not record audio or use a microphone.

## Try it

```sh
# Select and confirm a screen region; no model calls or memory changes.
uv run backseat preview

# Isolated observer → writer test, printed rather than spoken.
uv run backseat once --text-only --task "practicing binary search"

# Follow a task and inspect its evolving context, without writing jokes or audio.
uv run backseat run --fresh --observe-only --task "debugging my tests"

# Full companion. It asks resume/fresh when neither flag is supplied.
uv run backseat run

# Skip the memory question and continue the most recently saved task.
uv run backseat run --resume
```

`once` uses saved humor preferences but has isolated task history and never saves
its observations, jokes, or task back to disk. If the observer finds no new certain
event, it exits without calling the writer or speech provider.
Add `--force-remark` to request a remark even without a new event.

Use `--monitor 1` to skip the display prompt. Escape cancels selection. The preview
lets you retry/cancel before model calls, and Backseat finishes removing its own
windows before capturing. On an ultrawide, select your editor and/or problem rather
than the whole display. Fractional crop coordinates handle display scaling. The
preview uses the same proportional resizing as the model input (maximum edge 2560
pixels by default); check that code is readable.

`run` asks **resume last task** or **start fresh**, after region confirmation and
before the command-reader thread starts. Fresh excludes prior task history from
model prompts but retains humor preferences. `--fresh --task TEXT` names a new task;
`--resume` continues the last task. They are mutually exclusive. Noninteractive
runs require an explicit flag and should supply `--monitor` to skip the display prompt.

## CLI reference

```sh
uv run backseat {preview,once,run} [FLAGS]
uv run backseat --help
```

| Command | Behavior |
| --- | --- |
| `preview` | Select and confirm a crop without model calls or task-memory changes. |
| `once` | Observe one crop and optionally write/speak one remark. Uses saved taste but isolated, unsaved task history. |
| `run` | Keep observing until you quit. Save task context and feedback locally. |

| Flag | What it does |
| --- | --- |
| `-h`, `--help` | Show all commands and flags, then exit. |
| `--monitor NUMBER` | Choose a display by its 1-based number; omit to list displays and prompt. You still select and confirm a rectangle. |
| `--config PATH` | Load a TOML configuration file instead of `backseat.toml` in the current directory. Missing files use built-in defaults, including 30-second checks and a 60-second cooldown. |
| `--interval SECONDS` | Override both observation checks and the speech cooldown for this process. Accepts finite numbers ≥1, including decimals. Does not edit the TOML file or force regular speech. |
| `--chatty` | Testing mode: request commentary on every observation interval, including unchanged screens. Bypass the normal speech cooldown and writer silence; allow repeated remarks. |
| `--force-remark` | Testing mode: force a remark on the first capture. Works with `once` and `run`; subsequent `run` cycles use ordinary behavior unless `--chatty` is also set. |
| `--text-only` | Print observations and selected jokes; skip ElevenLabs and audio. Only the OpenAI key is required. |
| `--observe-only` | Run the observer without the writer or speech. Print current activity; save task context in `run`, or inspect the full context with `memory`. Only the OpenAI key is required. |
| `--save-captures DIRECTORY` | Opt into saving submitted, original-resolution crops as PNGs. In `preview`, save the confirmed crop as `preview.png`. Use `captures` to stay within the existing Git ignore rule. |
| `--resume` | In `run`, continue the most recently saved task without asking. If none exists, start a new task. Cannot combine with `--fresh` or `--task`. |
| `--fresh` | In `run`, start new task history without asking. Retain humor preferences and archived tasks. Cannot combine with `--resume`. |
| `--task "TEXT"` | Name a new task in `run`, or supply the goal for an isolated `once` test. Accepts 1–2000 characters; quote text containing spaces. Use with `--fresh` for an unambiguous new run. |

`--observe-only` takes priority over `--text-only`, `--chatty`, and `--force-remark`:
the writer is skipped entirely. `preview` makes no model calls, even with testing flags.
`--resume` and `--fresh` are rejected for `once` and `preview`. Timing flags are most
useful for `run`; `once` still captures only once, and `preview` does not schedule work.

Examples:

```sh
# Continue the last task with 10-second testing timing.
uv run backseat run --resume --interval 10

# Request a remark every 10 seconds, including on idle/unchanged screens.
uv run backseat run --resume --interval 10 --chatty

# Force a single spoken remark; add --text-only to print it instead.
uv run backseat once --force-remark

# Force the first remark, then continue with ordinary observation/silence behavior.
uv run backseat run --resume --force-remark

# New practice task on display 1, with 20-second checks/cooldown and printed jokes.
uv run backseat run --fresh --task "practicing binary search" --monitor 1 --interval 20 --text-only

# Inspect one crop and its commentary, saving the crop for debugging.
uv run backseat once --text-only --save-captures captures

# Use settings from a separate TOML file you've created.
uv run backseat run --fresh --config my-backseat.toml
```

## Controls

Type a command in the terminal and press Enter:

- `pause` / `resume`: stop/restart capture and new requests. Pause stops audio and
  discards pending output.
- `region` / `region 2`: reselect on the current/second display. Keep task memory,
  reset image comparison, and return to the previous paused/running state. Cancel
  keeps the existing region.
- `task practicing binary search`: save the old task and start a new one.
- `context this is a practice exercise, not my production code`: give it context
  that the screenshot alone cannot establish.
- `taste less theatrical, more dry understatement`: save a humor preference.
- `funny` / `boring`: rate the last delivered remark. Save the rated joke as a style
  example, including across fresh tasks. This is prompt feedback, not model training.
- `correct that's a stub, not a failed implementation`: correct task understanding,
  mark prior events unreliable, and discard pending jokes. Rebuild factual context
  from subsequent screenshots.
- `memory`: explicitly print the compact task context and humor preferences locally.
- `stats`: show observer and writer requests, tokens, and average latency separately,
  plus speech requests and characters.
- `quit` / Ctrl+C: stop audio and exit.

Text commands accept 1–2000 characters and preserve case. Previously submitted HTTP
requests cannot be recalled; shutdown waits for them to finish or time out. Their
output will not be applied or played after invalidation.

## What it remembers and how it writes

The **observer** gets the current and previous successfully observed screenshot,
the task's goal, a running summary, unresolved issues, and recent events. It records
meaningful changes with visible evidence and timestamps. Uncertain interpretations
are retained as uncertain and cannot trigger jokes. Resolved events remain historical
facts that can support callbacks. It cannot see what happened between samples or
while it was stopped.

The **writer** gets text context and a fresh certain event, without screenshots.
New certain events reach the writer even when the observer doesn't mark them
especially noteworthy; the writer decides whether there's a joke worth saying.
It generates three candidate remarks and selects one, or chooses silence. The prompt
encourages concrete details, understatement, mock concern, callbacks, and occasional
celebration, with a 35-word limit. Each candidate references supporting event IDs;
the runtime rejects unknown/unreliable references and exact repeated delivered jokes.
Candidates are validated individually: an overlong or otherwise invalid unused joke
does not discard a usable selection. If the selection is invalid, another valid
candidate can be used; a batch with no usable candidates is rejected.
Recently used premises and feedback help discourage repetitive phrasing.

Prompt context includes a compact summary, the last 20 valid events, the last 20
remarks, and up to 20 explicit context/taste notes. Saved records may be longer;
they are not all resent every request. Resume keeps original timestamps and starts
with no previous screenshot, so yesterday's failures are not visual evidence of
what is happening now. The first new screenshot can re-establish a familiar scene
as a current observation; its earlier timestamp stays in history. Restoring memory
alone does not trigger an old joke.

Model-written summaries and jokes can still be inaccurate, repetitive, or unfunny.
Event references establish which recorded facts were available, not whether every
sentence is true. Use `correct`, `boring`, and `taste` to refine the result. The
prompts are in `src/backseat/providers.py`.

## Timing and configuration

The checked-in `backseat.toml` uses **10-second observation checks** and **at least
10 seconds after speech finishes before another remark** for testing. Restore
`sample_seconds = 30` and `cooldown_seconds = 60` for quieter use.

Set both values for a single run with `--interval SECONDS`, without changing the file:

```sh
uv run backseat run --resume --interval 10
```

The value must be at least 1 second. Omit the flag to use the configured values.
This controls observation checks and the minimum gap after speech, rather than
forcing a remark every interval.

For testing, add **`--chatty`** to request commentary at each observation interval,
including unchanged screens. **`--force-remark`** forces only the first cycle.
Both ask the writer to speak; if it chooses silence, supplies an unusable candidate,
or returns malformed structured output,
Backseat uses a short generic narrator line. Factual evidence checks remain in place;
testing requests are labeled as narrator controls, never as desktop progress.
If the observer output is rejected, testing mode supplies no current scene facts to
the writer and keeps the previous successful screenshot for image comparisons.
These modes bypass the normal post-speech cooldown and exact-repeat rejection.
API errors still report failures. Speech never overlaps, requests are not queued,
and slow calls or long audio can stretch the gap beyond the requested interval.
Pause, quit, corrections, region changes, and output expiration still apply.

Observation continues while speech is being generated/played and during cooldown.
Each stage has one worker; missed intervals and remarks are not queued. Essentially
unchanged screenshots skip the observer call in ordinary mode. Small changes accumulate against the
last successfully observed image. The writer runs only for an eligible fresh, certain event;
there is no guaranteed remark every interval.

Voice mode also prints brief pipeline status: whether the observer found certain
events, skipped the writer, began writing, or the writer chose silence. These status
lines show counts and decisions, without printing screen contents. If `stats` shows
observer requests but no writer requests, check these lines: no new certain events,
`--observe-only`, or speech/cooldown can keep the writer from running. `resume`
unpauses; it does not force a joke or bypass these conditions.

Rejected model responses are reported as **model output rejected**, with a fixed
reason such as a bad selection, missing structured output, or an unknown event
reference. No response text or validation inputs are logged. These are separate
from connection, authorization, or quota failures. Forced-mode writer fallbacks
do not retry the model request.

Pause, corrections, task/region changes, and preferences/feedback changes invalidate
pending work. Output older than `stale_seconds`, referencing a resolved trigger, or
superseded by a newer noteworthy event is discarded before playback. A remark already
playing is allowed to finish when the observer discovers a new event. Audio interrupted
by pause/quit is marked partial; completion is confirmed when `afplay` exits successfully.

GPT-5.4 mini is initially used for both stages. `observer_model` and `writer_model`
override them independently; `model` remains the fallback for older configs. Speech
still uses your configured ElevenLabs voice and `speech_model`. Timeouts are bounded;
failed observations can be retried on a later interval, while writer/speech failures
are not immediately retried for the same event.

All supported TOML settings:

| Setting | Checked-in value | Meaning |
| --- | --- | --- |
| `model` | `"gpt-5.4-mini"` | Fallback OpenAI model when a stage override is omitted. |
| `observer_model` | `"gpt-5.4-mini"` | OpenAI model that interprets screenshots and updates context. |
| `writer_model` | `"gpt-5.4-mini"` | OpenAI model that writes and selects jokes. |
| `speech_model` | `"eleven_v4"` | ElevenLabs synthesis model; the voice ID comes from `.env`. |
| `sample_seconds` | `10` | Seconds between eligible local screen checks, minimum 1. |
| `cooldown_seconds` | `10` | Minimum gap after speech ends before the next remark; 0 or more. |
| `change_threshold` | `0.02` | Fraction of thumbnail pixels that must change brightness by more than 20 before calling the observer; greater than 0 and at most 1. |
| `image_max_edge` | `2560` | Maximum input-image width/height after proportional resizing; minimum 256 pixels. |
| `api_timeout_seconds` | `20` | Provider timeout in seconds; must be positive. |
| `stale_seconds` | `60` | Maximum age of a capture/event's pending output before it is discarded; must be positive. |

`--interval` takes priority over `sample_seconds` and `cooldown_seconds` loaded from
the chosen file. Other settings stay as configured. Configuration and `.env` paths
are resolved from the directory where you launch Backseat. Ending terminal input
(EOF) quits a running session, just like `quit`.

## Local data and usage

Task summaries, contextual events, delivered remarks, feedback, and explicit humor
preferences are saved in **`.backseat/memory.sqlite`**. This is local SQLite, without
telemetry or an external memory service. Task changes archive previous records, but
resume currently loads only the most recently updated task. Starting fresh does not
delete archived tasks. After quitting Backseat, deleting `.backseat` resets all memory.
Unreadable/unsupported databases are reported rather than replaced; save failures pause
the companion rather than silently dropping context.

Screenshots remain in memory unless you opt into `--save-captures captures`. This
saves submitted crops (or the confirmed preview) for debugging. Only cropped images
are sent to OpenAI. Model-generated text summaries may include details visible in
those crops; they are saved locally and sent as context on later calls. ElevenLabs
receives only the selected remark. Memory, `.env`, captures, and environments are
ignored by git. OpenAI requests use `store=false`; provider retention policies still
apply. No microphone, automatic filesystem access, or computer control is included.

Two-stage processing and previous-image comparisons can use more tokens than the
original prototype. Silence still costs an observer request, and writer silence costs
a writer request. Session stats include calls whose output is later discarded; failed
requests may not return token counts. Use provider dashboards for actual billing and
spending limits. No automatic dollar estimate is provided.

## Verification

```sh
uv run pytest
uv run ruff check .
```

Tests use fake providers and images, plus offscreen Qt windows. They do not capture
your desktop or spend credits. They cover event transitions, bounded context, saved
memory and resume/fresh behavior, corrections, observation during audio/cooldown,
stale/unsupported output, failures, feedback, and capture-window cleanup.
Forced-mode tests cover unchanged screens, silence fallback, repeated cycles,
malformed-response fallback, one-time forcing, pause, expiration, and non-overlapping
audio. Mock HTTP tests exercise the real SDK's structured parsing, including a batch
with overlong unused candidates.

Before judging the experience, audition failed→passed tests, returning to an earlier
problem, browser switching, and idle-screen silence in text mode, then with your
voice. Check factual grounding, joke repetition, callbacks, and interruptions. A
15-minute real session can measure latency and usage; unit tests do not establish
humor quality or end-to-end performance on your G9.
