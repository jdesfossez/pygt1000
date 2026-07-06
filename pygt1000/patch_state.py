"""The device state model.

``PatchState`` is the one owner of the known effect state. Previously
``current_state`` was a raw nested dict mutated from five sites under an ad-hoc
lock, and "is it initialised yet" was the fragile ``len(current_state) - 1 ==
len(fx_types)`` expression. Here the dict, its lock, and ``last_sync_ts`` are
internal invariants, reached only through a narrow interface:

- ``apply(decoded)`` folds a decoded inbound value into state (SW / TYPE /
  sliders) and reports what happened via an :class:`ApplyResult`.
- ``set_fx(fx_type, fx_id, field, value)`` records a local change we just sent.
- ``record_scan(fx_type, states, ts)`` installs the result of a full block scan.
- ``set_sliders(fx_type, fx_id, slider1, slider2)`` records a slider re-read.
- ``snapshot()`` returns a consistent copy for reading.
- ``is_ready()`` replaces the ``len(...) - 1`` idiom with an explicit check.

The state shape is ``{"last_sync_ts": {fx_type: datetime}, fx_type: [fx, ...]}``
where each ``fx`` is an :class:`FxBlock` (its ``slider1``/``slider2`` are
:class:`SliderValue` or None). Boundary contract: ``snapshot()``
returns a deep copy of that structure *with the typed records intact* — callers
read ``fx.state`` / ``fx.slider1.value``, not string keys — so a wrong field is
a type error rather than a silent ``KeyError``.
"""

import copy
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple, Optional, Union

from .address_map import DecodedValue

logger = logging.getLogger(__name__)


@dataclass
class SliderValue:
    """One resolved slider: its current ``value`` (``None`` if the read
    failed / was skipped), the ``label`` naming the param it controls, and the
    param's ``min``/``max`` range from the AddressMap. Produced by
    :meth:`pygt1000.block_snapshot.BlockSnapshot._resolve`; carried on an fx
    block's ``slider1``/``slider2`` (below) and mutated in place by
    :meth:`PatchState.apply` when the device echoes a new value. Mutable for that
    in-place update. It lives here — with :class:`FxBlock`, its storage owner —
    rather than next to its producer, because ``BlockSnapshot`` already imports
    ``FxBlock`` from this module and defining it there would make the import
    circular."""

    value: Optional[int]
    label: str
    min: int
    max: int


@dataclass
class FxBlock:
    """One fx block's presented state. Assembled by
    :meth:`pygt1000.block_snapshot.BlockSnapshot.snapshot`, stored in
    ``PatchState``'s state, mutated in place by ``set_fx`` / ``set_sliders`` /
    ``apply``, and handed back (deep-copied) by ``snapshot()``. Mutable for those
    in-place updates.

    ``fx_id`` is a str for id-addressed blocks and may be an int on the fx path
    (it passes through ``normalize_block`` unchanged); it is compared via
    ``str(...)``. ``slider1``/``slider2`` are :class:`SliderValue` or None."""

    fx_id: Union[str, int]
    state: Optional[str] = None
    name: Optional[str] = None
    slider1: Optional[SliderValue] = None
    slider2: Optional[SliderValue] = None


class ApplyResult(NamedTuple):
    """Outcome of folding one decoded value into the state.

    ``matched`` is whether the value landed on a known fx block; ``type_changed``
    is whether it was a TYPE change (the caller reacts by re-reading sliders and
    refreshing the resolved fx name).
    """

    matched: bool
    type_changed: bool


class PatchState:
    def __init__(self, fx_types):
        self._fx_types = fx_types
        # Guards the state dict and prevents changes while a refresh is running.
        self._lock = threading.Semaphore(1)
        self._state = {"last_sync_ts": {}}
        # The single owner of "which effect is loaded in fx block N". Keyed by a
        # normalized (str) fx_id so a caller passing an int and one passing a str
        # resolve the same effect. Written by the block scan (set_fx_name) and
        # the device-echo TYPE-change path (folded into apply); read by Slider,
        # BlockReader, and the fx write path.
        self._fx_names = {}

    def is_ready(self):
        """True once every fx type has an entry (a full scan has completed)."""
        with self._lock:
            return self._is_ready_locked()

    def _is_ready_locked(self):
        # Caller must hold ``self._lock``. One entry per fx type, plus the
        # ``last_sync_ts`` bookkeeping key, means the initial scan is complete.
        return (len(self._state) - 1) == len(self._fx_types)

    def snapshot(self):
        """A consistent deep copy of the state, safe to read without the lock."""
        with self._lock:
            return copy.deepcopy(self._state)

    def record_scan(self, fx_type, states, ts):
        """Install the result of a full block scan for one fx type."""
        with self._lock:
            self._state[fx_type] = states
            self._state["last_sync_ts"][fx_type] = ts

    def set_fx(self, fx_type, fx_id, field, value):
        """Record a local change we just sent to the unit.

        A block with a single instance is addressed by index (its ``fx_id`` may
        be empty); otherwise the entry is matched by ``fx_id``.
        """
        with self._lock:
            entries = self._state[fx_type]
            if len(entries) == 1:
                setattr(entries[0], field, value)
            else:
                for entry in entries:
                    if str(entry.fx_id) == str(fx_id):
                        setattr(entry, field, value)

    def set_fx_name(self, fx_id, name):
        """Record the resolved effect name loaded in an fx block.

        The key is normalized to ``str`` so int/str callers land on the same
        block. This is the single write target for the block scan; the
        device-echo TYPE-change path writes it through :meth:`apply`.
        """
        with self._lock:
            self._set_fx_name_locked(fx_id, name)

    def _set_fx_name_locked(self, fx_id, name):
        # The single internal write target for the resolved fx name. Caller must
        # hold ``self._lock``. Both public write paths (``set_fx_name`` and the
        # device-echo TYPE-change branch in ``apply``) route through here so the
        # str-key normalization lives in exactly one place.
        self._fx_names[str(fx_id)] = name

    def fx_name(self, fx_id):
        """The resolved effect name loaded in an fx block (``KeyError`` if the
        block has not been scanned yet). Key normalized to ``str``."""
        with self._lock:
            return self._fx_names[str(fx_id)]

    def set_sliders(self, fx_type, fx_id, slider1, slider2):
        """Record a re-read of an fx block's two sliders."""
        with self._lock:
            for fx in self._state[fx_type]:
                if str(fx.fx_id) == str(fx_id):
                    fx.slider1 = slider1
                    fx.slider2 = slider2
                    break

    def apply(self, decoded: DecodedValue):
        """Fold a decoded inbound value into state.

        Refuses to touch state until the first full scan has completed
        (:meth:`is_ready`). Updates the matching fx block's SW, TYPE (name), or a
        matching slider value, and bumps ``last_sync_ts`` when anything matched.
        """
        now = datetime.now()
        with self._lock:
            if not self._is_ready_locked():
                return ApplyResult(matched=False, type_changed=False)
            fx_type = decoded.fx_type
            for fx in self._state[fx_type]:
                if str(fx.fx_id) != str(decoded.fx_id):
                    continue
                matched = False
                type_changed = False
                if decoded.value_name == "SW":
                    logger.info(
                        f"{fx_type}{decoded.fx_id}: "
                        f"{fx.state} -> {decoded.str_value}"
                    )
                    fx.state = decoded.str_value
                    matched = True
                elif decoded.value_name == "TYPE":
                    logger.info(
                        f"{fx_type}{decoded.fx_id}: "
                        f"{fx.name} -> {decoded.str_value}"
                    )
                    fx.name = decoded.str_value
                    # The fx block also feeds the resolved-effect-name owner, so
                    # the echo path has one write target, not a second dict kept
                    # in sync by the facade.
                    if fx_type == "fx":
                        self._set_fx_name_locked(decoded.fx_id, decoded.str_value)
                    matched = True
                    type_changed = True
                else:
                    if (
                        fx.slider1 is not None
                        and fx.slider1.label == decoded.value_name
                    ):
                        fx.slider1.value = decoded.int_value
                        matched = True
                    if (
                        fx.slider2 is not None
                        and fx.slider2.label == decoded.value_name
                    ):
                        fx.slider2.value = decoded.int_value
                        matched = True
                if matched:
                    self._state["last_sync_ts"][fx_type] = now
                return ApplyResult(matched=matched, type_changed=type_changed)
        return ApplyResult(matched=False, type_changed=False)
