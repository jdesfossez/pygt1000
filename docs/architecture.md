# Architecture: how `GT1000` wires the seams together

`GT1000` (`pygt1000/gt1000.py`) is a **facade**. It owns almost no state; it
constructs a set of narrow, single-responsibility collaborators and translates
between them and the caller. Each collaborator's own module docstring explains
what that seam owns and why it exists in isolation — read those first for the
"what." This page is the **wiring** view: the construction order in
`GT1000.__init__`, and — the part that lives nowhere else — *which seam is
injected into which, and why the dependency points that way.*

The recurring rule is: **each seam owns one concept and its device dependency is
injected**, so every module is testable without the MIDI wire (inject a
`FakeTransport`, a fake value-reader, or drive a handler directly). The
constructor is where those injections are made; keep it reading as wiring and
keep the rationale here.

## Construction order and injected dependencies

The order matters: later seams are handed methods of earlier ones as their
injected device dependency.

1. **`RefreshScheduler`** (`self._refresh`) — the background refresh
   coordination (queue + wakeup event + worker). `GT1000` supplies the *work* as
   two per-type handlers and calls `submit()` to enqueue:
   - `{type: "full"}` → `_refresh_full`: scan all the blocks.
   - `{type: "sliders", fx_type, fx_id}` → `_refresh_sliders`: re-read just the
     sliders for one fx block.

   The scheduler carries no MIDI knowledge; the device work stays in the
   handlers on the facade.

2. **`KeepAlive`** (`self._keepalive`) — the periodic device-liveness poll. The
   device probe is injected as a `poll` lambda (a `fetch_mem` of the editor-mode
   liveness address), and the "device is unresponsive" action is injected as
   `on_unresponsive = lambda: self._editor_session.reopen()`. KeepAlive itself
   carries no wire or port knowledge, and the reopen it triggers is the *same*
   path `open()` runs — the reopen policy and the open policy are one method, not
   two kept in sync. (The `EditorSession` it reopens is built in step 11; the
   lambda defers the reference until call time.)

3. **`AddressMap`** (`self._address_map`) — owns the spec-table load and the
   encode path. Built from `self.fx_types`. `GT1000` keeps backward-compatible
   accessors that delegate to it.

4. **`SysExCodec`** (`self._codec`) — the SysEx wire format (DT1/RQ1 framing,
   checksum, reply parsing). Stateless; `GT1000` supplies the negotiated
   `device_id` per call instead of hand-rolling frames.

5. **`PatchState`** (`self._state`) — the single owner of the known device state
   (the dict, its lock, `last_sync_ts`). `GT1000` only translates decoded
   messages and local changes into calls on it.

6. **`BlockReader`** (`self._block_reader`) — the read-side pipeline for one
   setting (address → fetch → decode). The device read is injected as
   `self.fetch_mem` so the module carries no wire knowledge; the resolved fx name
   is read live through `self._state.fx_name` — `PatchState` is the owner of that
   fact.

7. **`Slider`** (`self._slider`) — slider policy (which two params a block
   exposes, the eq special case) plus resolution (range from `AddressMap`,
   current value read over MIDI). The per-param value read is injected as
   `self._block_reader.read_value` so `Slider` carries no device knowledge; the
   resolved fx name is again read live through `self._state.fx_name`.

   > **Why `Slider` depends on `BlockReader` (not the reverse):** `Slider` needs
   > to read a param's current value, and `BlockReader` already owns "read one
   > setting." So `Slider` is constructed *after* `BlockReader` and is handed its
   > `read_value`. This direction is what forces the `BlockSnapshot` split below.

8. **`BlockSnapshot`** (`self._block_snapshot`) — "read a whole block": assemble
   state + name + both sliders, handle the ns/delay no-TYPE special case, and
   iterate the blocks of an fx type. It reads settings through `BlockReader`,
   resolves sliders through `Slider`, and records the block's resolved fx name
   through `self._state.set_fx_name` (`PatchState`, the owner of that fact).

   > **Why this is a separate module and not a method on `BlockReader`:**
   > `Slider` is constructed with `BlockReader.read_value` (step 7), so
   > `BlockReader` must not depend on `Slider`. Folding the whole-block assembly
   > (which needs `Slider`) back into `BlockReader` would make that dependency
   > circular. A third module built *after* both is the clean seam.

9. **`Transport`** (`self._transport`) — the seam to the MIDI wire. Production
   uses `RtMidiTransport`; tests inject a `FakeTransport`. This is the only
   layer that imports `rtmidi`, and it is imported lazily inside
   `RtMidiTransport` so tests never load the compiled binding.

10. **`DeviceLink`** (`self._link`) — the request/response conversation on top of
    `Transport`: offset-keyed correlation, the semaphore, and the wait/retry
    loop. It installs its own inbound callback on the transport and negotiates
    the device id from identity replies. `GT1000` wires two callbacks into it:
    - `on_unsolicited(self._process_data_from_unit)` — device-emitted frames.
    - `on_identity(self._apply_identity)` — parsed identity replies.

    The negotiated `device_id` lives on the link; `GT1000.device_id` is a
    property that reads/writes it there so existing callers and the codec keep
    seeing `gt.device_id`.

11. **`EditorSession`** (`self._editor_session`) — the device bring-up *sequence*
    (identity retry, editor-mode set, liveness check) and the reopen policy. It
    owns the `Transport` lifecycle (open/close) and drives `DeviceLink`. The
    facade's `open_ports` / `close_ports` are thin delegates, and it is the
    `reopen` target `KeepAlive` was handed back in step 2.

    > **Why the model-specific tweak stays on the facade:** the GT-1000CORE has
    > 3 fx blocks where the others have 4. That fix is applied in
    > `_apply_identity` (the `on_identity` callback), from the negotiated
    > identity reply, as it lands during the open sequence — before the
    > editor-mode set. `EditorSession` surfaces the identity but does not know
    > the block layout, so the layout knowledge stays here rather than leaking
    > into the session.

12. **`ChainCodec`** (`self._chain_codec`) — the effect chain's whole device
    round trip (byte-list address, int↔name conversion, parse/serialize). The
    device dependency is injected on both sides — `self.fetch_mem` to read,
    `self._link.set` to write — so the facade's chain methods are thin delegates.

## The facade's own remaining jobs

After wiring, `GT1000` keeps only the translation logic that genuinely spans
seams:

- `_apply_identity` — the model substitution and the CORE fx-block-count tweak
  (see step 11).
- `_process_data_from_unit` — route a device frame: a program-change offset
  submits a `full` refresh; otherwise `decode` → `PatchState.apply`, and on a
  TYPE change submit a `sliders` re-read for that block.
- `refresh_state` / `_refresh_full` / `_refresh_sliders` — the device work the
  scheduler dispatches to.
- the thin delegating methods (`open_ports`, `close_ports`,
  `get_all_fx_type_states`, `get_one_fx_state`, the chain methods, …).
