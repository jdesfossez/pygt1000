# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`pygt1000` is a Python library for driving a BOSS GT-1000 / GT-1000CORE guitar
effects processor over MIDI (SysEx). It talks to the device by reading and
writing addresses in the unit's memory map, using human-readable option names
instead of hand-crafted hexadecimal.

## Commands

The project uses Poetry. The system `python3` does not have the dependencies —
always go through `poetry run`.

```bash
poetry install                       # set up the dev environment
poetry run pytest -q                 # full test suite (~217 tests, fast)
poetry run pytest tests/test_slider.py                       # one file
poetry run pytest tests/test_slider.py::test_name            # one test
poetry run pytest -k substring                               # by name match
poetry run ruff check pygt1000 tests # lint (keep clean)
poetry run mypy pygt1000             # type check — see note below
```

`mypy` currently reports **5 pre-existing errors** (3 in `chain.py` around the
`Block`/`List` variance, 2 `import-not-found` for `rtmidi` in `transport.py`).
These predate current work; a change is "clean" if it adds no *new* errors, not
if mypy is silent.

Tests never touch real MIDI: `tests/conftest.py` builds every `GT1000` on a
`FakeTransport`, and `rtmidi` is imported lazily only inside `RtMidiTransport`,
so the compiled `python-rtmidi` binding is never loaded during tests.

## Architecture

`GT1000` (`pygt1000/gt1000.py`) is a **facade** that wires together a set of
narrow, single-responsibility collaborators and translates between them. It owns
almost no state itself. Read `GT1000.__init__` first — its comments name each
seam with a one-line pointer; the full wiring narrative (construction order and
the cross-seam dependency injections, i.e. which seam is injected into which and
why) lives in [`docs/architecture.md`](docs/architecture.md). The collaborators:

- **`Transport`** (`transport.py`) — the MIDI wire seam: `send(message)` + an
  `on_receive` callback. `RtMidiTransport` is production; `FakeTransport` records
  sends and injects canned replies for tests. Nothing above this layer imports
  `rtmidi`.
- **`SysExCodec`** (`sysex_codec.py`) — pure bytes-in/bytes-out wire format:
  DT1 (set) / RQ1 (request) framing, checksum, and parsing data/identity
  replies. Stateless; the negotiated `device_id` is passed per call.
- **`DeviceLink`** (`device_link.py`) — the request/response conversation on top
  of `Transport`: send a request, block for the matching reply (correlated by
  `tuple(address)`), route everything else to an unsolicited handler, and
  negotiate `device_id` from identity replies.
- **`AddressMap`** (`address_map.py`) — loads the spec tables and owns the
  **encode path**: `address_for(section, option, setting, value)`,
  `value_range(...)`, `start_section` / `fx_start_section`, and the reverse
  `decode(address, value)` (exposed on `GT1000` as `lookup`). The six spec
  registries are private; reach them only through its accessors.
- **`PatchState`** (`patch_state.py`) — the single owner of known device state
  (the dict, its lock, `last_sync_ts`). Mutated only via `apply` / `set_fx` /
  `record_scan` / `set_sliders`; read via `snapshot`.
- **`Slider`** (`slider.py`) — slider *policy* (which two params a block exposes,
  the eq special case) + *resolution* (range from `AddressMap`, current value via
  an injected reader). The device read is injected so it resolves against a fake
  reader without a wire.
- **`RefreshScheduler`** (`refresh_scheduler.py`) — the background refresh
  worker: a task queue + wakeup event + lock as one invariant. `GT1000` registers
  `full` / `sliders` handlers and calls `submit(task)`; the scheduler carries no
  MIDI knowledge and is drivable in tests without a thread.
- **`chain.py`** — the effect-chain object model (`EffectBlock`, `DividerBlock`)
  and pure `parse_chain` / `serialize_chain` between the word-list and object
  representations.

The recurring design rule across these modules: **each seam owns one concept and
the device dependency is injected**, so every module is testable without the wire
(inject `FakeTransport`, a fake value-reader, or call `drain()`/handlers
directly). When adding behavior, extend the module that owns the concept and keep
`GT1000` a thin translator — do not push protocol logic, framing, or state back
into the facade.

### Data-driven memory map

`pygt1000/specs/` holds the GT-1000 MIDI implementation as paired `*.txt`
(source, transcribed from the BOSS spec) and `*.json` (loaded at runtime by
`AddressMap`). The scripts in `scripts/` (`spec-to-json.py`,
`options-to-json.py`, `chain-elements.py`) regenerate the JSON from the text.
Known gap: options with a range wide enough to need multiple value bytes are not
fully handled — most operations are single-byte.

## Conventions

- **Issues/PRDs** live as markdown under `.scratch/<feature>/` (`PRD.md`, then
  `issues/NN-slug.md`, moved to `issues/done/` when complete). See
  `docs/agents/issue-tracker.md`. Commits are one PRD "slice" each, with commit
  messages that record key decisions, files changed, and notes for the next
  iteration — match that style.
- **Guardrail tests** are re-pointed at the seam when code moves (e.g. a test
  that called a now-inlined `GT1000` helper is updated to call
  `gt._address_map...` directly), preserving the exact assertions so behavior
  stays pinned.
- **The `README.md` usage examples are stale** — they reference methods removed
  during refactoring (`_get_start_section`, `build_dt_message`, `send_message`,
  `send_command`). Trust the code and tests over the README for the current API;
  `examples/chain.py` and `lookup.py` reflect real current usage.
