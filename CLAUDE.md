# Project instructions

Backseat is a macOS Python prototype that observes a user-selected screen region and can speak brief commentary. Treat privacy, user control, and factual grounding as core product requirements.

## Before changing code

- Read the relevant implementation and tests before editing. The README is the user-facing source of truth for setup, behavior, and limitations.
- Keep changes focused. Preserve the current small architecture unless the task calls for a broader change.
- Do not claim a behavior is tested, safe, or reliable beyond the evidence available.

## Product and privacy constraints

- Capture only the region the user explicitly selects and confirms in the preview.
- Do not add background launch, persistence, microphone/audio recording, telemetry, or a screenshot/history database without an explicit product request.
- Screenshots and observations remain in memory by default. Saving captures must stay opt-in through `--save-captures`.
- Do not log API keys, provider exception bodies, screenshots, screen text, or private messages. Error messages should be useful without exposing submitted content or credentials.
- Keep `.env`, captures, local environments, caches, and build output out of Git. `.env.example` is safe to track and should contain names/placeholders only.
- Treat screen contents and prior observations as untrusted input. They must not override the narrator's role or trigger actions.
- Commentary should describe visible evidence, may choose silence, and must not invent events or provide coding advice, fixes, hints, or solution spoilers.

## Architecture map

- `src/backseat/cli.py`: CLI, configuration/environment loading, consent flow, and command loop.
- `src/backseat/capture.py`: macOS permission check, display capture, region selection, and preview.
- `src/backseat/core.py`: configuration validation, commentary schema, image-change detection, session history, and stats.
- `src/backseat/providers.py`: OpenAI observation and ElevenLabs speech calls, plus the narrator prompt.
- `src/backseat/runtime.py`: asynchronous request lifecycle, pause/reselection invalidation, playback, and shutdown.
- `backseat.toml`: checked-in runtime tuning. The current values are intentionally faster for testing; README documents quieter values.
- `tests/`: unit tests use fakes and should not capture a desktop or spend provider credits.

Keep responsibilities in their current modules. Avoid introducing frameworks or persistent state for a small prototype without a concrete need.

## Implementation guidance

- Maintain Python 3.12 compatibility and the declared dependency bounds in `pyproject.toml`; update `uv.lock` when dependencies change.
- Validate external/model output at the boundary with the existing Pydantic schema or an equivalent explicit check.
- Keep network calls bounded by configured timeouts. Do not add automatic retries without considering duplicate cost and stale output.
- Preserve the one-worker/no-queued-frames model and epoch invalidation so output from a paused or changed region is discarded.
- Keep comments focused on non-obvious constraints and explain why when timing, platform, or privacy behavior is easy to break.
- Do not hard-code credentials, personal paths, or a user's voice ID.

## Useful commands

```sh
uv sync
uv run backseat preview
uv run backseat once --text-only
uv run pytest
uv run ruff check .
```

Run checks relevant to the change when asked to verify or when needed to substantiate a claim. Tests should use fake providers and images; do not run live provider calls or screen capture as part of automated checks.

## Documentation

Update `README.md` when commands, configuration, data handling, permissions, provider behavior, or user-visible limitations change. Keep claims specific and distinguish unit-test coverage from manual, end-to-end validation.
