"""tracewire.core -- trace file format and delta compression codec.

Format overview
---------------
A trace file has three logical parts:

    1. header      -- magic, version, metadata, string table (names of
                      functions and variables are interned into a table
                      and referenced by index, which keeps the payload
                      small).
    2. event table -- a sequence of control-point events.  Payload bytes
                      are delta-encoded (see below) and the whole table is
                      zlib-compressed.
    3. trailer     -- end marker so readers can verify integrity.

Delta encoding
--------------
* Line numbers are encoded as the difference to the previous line inside
  the same function (signed varint), so walking down a function costs a
  few bytes per step.
* Variable snapshots are recorded *per changed variable* only: a snapshot
  event stores the list of variables whose value differs from the last
  snapshot taken in the same frame, together with their new values.  An
  unchanged variable costs zero bytes.
* Values are encoded by a small type discriminator followed by a length-
  prefixed payload; arbitrary objects fall back to a repr() encoding so
  the format stays dependency-free.

Only the events needed to rebuild the timeline and per-marker state are
stored; nothing here re-executes code, so the format is deliberately
simple and deterministic.
"""

from __future__ import annotations

import struct
import time
import zlib

# --------------------------------------------------------------------------
# constants

MAGIC = b"TWTR"
# v2 interns variable names into the string table (v1 wrote them inline on
# every event).  The reader rejects v1 files with an explicit message rather
# than silently misparsing them.
VERSION = 2

EV_LINE = 1        # a control point: one executed line, with a state delta
EV_CALL = 2        # function entered
EV_RETURN = 3      # function returned
EV_EXCEPTION = 4   # an exception was raised at this control point

T_NONE = 0
T_BOOL = 1
T_INT = 2
T_FLOAT = 3
T_STR = 4
T_BYTES = 5
T_REPR = 6         # anything else, stored as repr() encoded utf-8

_TYPES = {
    type(None): T_NONE,
    bool: T_BOOL,
    int: T_INT,
    float: T_FLOAT,
    str: T_STR,
    bytes: T_BYTES,
}

# Scalars are stored as-is; everything else is frozen to its repr() *when the
# event is recorded*.  Serialising lazily at write time would copy every value
# at the end of the run, so each control point would show the final state and
# in-place mutations would be invisible.
_SCALARS = (type(None), bool, int, float, str, bytes)

# A repr() of a large container can be megabytes and is repeated for every
# control point where the value changed. Bound it.
MAX_REPR = 4000


def freeze_value(value):
    """Return a stable, encode-ready copy of *value* as it is right now.

    Idempotent: scalars (including already-frozen strings) pass through, so
    callers may freeze early for change detection and the writer may freeze
    again defensively without cost.
    """
    if type(value) in _SCALARS:
        return value
    try:
        text = repr(value)
    except Exception as exc:  # a broken __repr__ must not kill the trace
        return f"<unreprable {type(value).__name__}: {exc}>"
    if len(text) > MAX_REPR:
        return text[:MAX_REPR] + f"… <truncated, {len(text)} chars>"
    return text


def freeze_mapping(values):
    return {name: freeze_value(value) for name, value in values.items()}

# --------------------------------------------------------------------------
# low-level helpers

class TraceFormatError(ValueError):
    """Raised when a trace file is truncated, corrupt or from a newer version."""


def _need(buf: bytes, pos: int, size: int) -> None:
    if pos < 0 or pos + size > len(buf):
        raise TraceFormatError(
            f"truncated trace: need {size} byte(s) at offset {pos}, file has {len(buf)}"
        )


def _u32(n: int) -> bytes:
    return struct.pack("<I", n)


def _read_u32(buf: bytes, pos: int):
    _need(buf, pos, 4)
    return struct.unpack_from("<I", buf, pos)[0], pos + 4


def _varint(n: int) -> bytes:
    """Unsigned varint (base-128, LSB first); arbitrary precision."""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varint(buf: bytes, pos: int):
    result = 0
    shift = 0
    while True:
        _need(buf, pos, 1)
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 70:  # 10 bytes is already absurd for a file offset
            raise TraceFormatError("corrupt trace: varint is too long")


def _svarint(n: int) -> bytes:
    """Signed varint: arbitrary-precision zigzag code (Google style)."""
    return _varint((n << 1) if n >= 0 else ((-n << 1) - 1))


def _read_svarint(buf: bytes, pos: int):
    u, pos = _read_varint(buf, pos)
    return (u >> 1) ^ -(u & 1), pos


# --------------------------------------------------------------------------
# string table

class StringTable:
    """Interns strings to ids; ids are written into the payload."""

    __slots__ = ("_names", "_index")

    def __init__(self):
        self._names = []
        # name -> id.  The previous implementation used list.index(), which is
        # O(n) per lookup; func_id() is called on *every* event, so on a trace
        # with a few thousand functions interning alone dominated the run
        # (measured: 0.23 s to intern 8000 unique names, 10 us per hot lookup).
        self._index = {}

    def intern(self, name: str) -> int:
        try:
            return self._index[name]
        except KeyError:
            idx = len(self._names)
            self._names.append(name)
            self._index[name] = idx
            return idx

    def name(self, idx: int) -> str:
        return self._names[idx]

    def __len__(self) -> int:
        return len(self._names)

    def dump(self) -> bytes:
        out = bytearray(_u32(len(self._names)))
        for name in self._names:
            data = name.encode("utf-8", "replace")
            out += _varint(len(data))
            out += data
        return bytes(out)

    @staticmethod
    def load(buf: bytes, pos: int):
        count, pos = _read_u32(buf, pos)
        names = []
        for _ in range(count):
            size, pos = _read_varint(buf, pos)
            names.append(buf[pos:pos + size].decode("utf-8", "replace"))
            pos += size
        tbl = StringTable()
        tbl._names = names
        tbl._index = {n: i for i, n in enumerate(names)}
        return tbl, pos


# --------------------------------------------------------------------------
# value encoding

def encode_value(value) -> bytes:
    vtype = _TYPES.get(type(value), T_REPR)
    if vtype == T_REPR:
        payload = repr(value).encode("utf-8", "replace")
    elif vtype == T_STR:
        payload = value.encode("utf-8", "replace")
    elif vtype == T_BYTES:
        payload = value
    elif vtype == T_INT:
        payload = _svarint(value)
    elif vtype == T_FLOAT:
        payload = struct.pack("<d", value)
    elif vtype == T_BOOL:
        payload = b"\x01" if value else b"\x00"
    else:  # T_NONE
        payload = b""
    return bytes([vtype]) + _varint(len(payload)) + payload


def decode_value(buf: bytes, pos: int):
    _need(buf, pos, 1)
    vtype = buf[pos]
    pos += 1
    size, pos = _read_varint(buf, pos)
    _need(buf, pos, size)
    payload = buf[pos:pos + size]
    pos += size
    if vtype == T_NONE:
        return None, pos
    if vtype == T_BOOL:
        return payload == b"\x01", pos
    if vtype == T_INT:
        return _read_svarint(payload, 0)[0], pos
    if vtype == T_FLOAT:
        if len(payload) != 8:
            raise TraceFormatError("corrupt trace: float payload is not 8 bytes")
        return struct.unpack("<d", payload)[0], pos
    if vtype == T_STR:
        return payload.decode("utf-8", "replace"), pos
    if vtype == T_BYTES:
        return payload, pos
    if vtype != T_REPR:
        raise TraceFormatError(f"corrupt trace: unknown value tag {vtype}")
    return payload.decode("utf-8", "replace"), pos  # T_REPR


# --------------------------------------------------------------------------
# events

class LineEvent:
    __slots__ = ("evtype", "func_id", "line", "vars")

    def __init__(self, evtype, func_id, line, vars_):
        self.evtype = evtype
        self.func_id = func_id
        self.line = line
        self.vars = vars_  # dict str->value, only *changed* variables

    def encode(self, prev_line: int, strings) -> bytes:
        out = bytearray([self.evtype])
        out += _u32(self.func_id)
        if self.evtype in (EV_LINE, EV_EXCEPTION):
            out += _svarint(self.line - prev_line)
        out += _varint(len(self.vars))
        for name, value in self.vars.items():
            out += _varint(strings.intern(name))
            out += encode_value(value)
        return bytes(out)


class CallEvent:
    __slots__ = ("evtype", "func_id", "line", "vars")

    def __init__(self, func_id, line, vars_):
        self.evtype = EV_CALL
        self.func_id = func_id
        self.line = line
        self.vars = vars_  # initial frame state (function arguments)

    def encode(self, prev_line: int, strings) -> bytes:
        out = bytearray([EV_CALL])
        out += _u32(self.func_id)
        out += _svarint(self.line - prev_line)
        out += _varint(len(self.vars))
        for name, value in self.vars.items():
            out += _varint(strings.intern(name))
            out += encode_value(value)
        return bytes(out)


class ReturnEvent:
    __slots__ = ("evtype", "func_id")

    def __init__(self, func_id):
        self.evtype = EV_RETURN
        self.func_id = func_id

    def encode(self, prev_line: int, strings) -> bytes:
        out = bytearray([EV_RETURN])
        out += _u32(self.func_id)
        return bytes(out)


# --------------------------------------------------------------------------
# writer / reader

class TraceWriter:
    """Collects events in memory and writes a delta-compressed trace file.

    Call depth is tracked alongside every event: line deltas and frame
    state are keyed by ``(func_id, depth)`` so that recursive invocations
    of the same function never contaminate each other.
    """

    def __init__(self, srcfile: str, modules=None, started=None):
        self.srcfile = srcfile
        self.modules = modules or []
        self.started = started if started is not None else time.time()
        self.strings = StringTable()
        self.events = []
        self._func_ids = {}   # qualname -> id
        # Filled in by tracewire.tracer.record(): the traced program's outcome.
        self.error = None
        self.exit_code = 0

    def func_id(self, qualname: str) -> int:
        try:
            return self._func_ids[qualname]
        except KeyError:
            idx = self.strings.intern(qualname)
            self._func_ids[qualname] = idx
            return idx

    def add_line(self, qualname: str, line: int, changed_vars) -> None:
        self.events.append(
            LineEvent(EV_LINE, self.func_id(qualname), line, freeze_mapping(changed_vars or {}))
        )

    def add_exception(self, qualname: str, line: int, changed_vars) -> None:
        self.events.append(
            LineEvent(EV_EXCEPTION, self.func_id(qualname), line, freeze_mapping(changed_vars or {}))
        )

    def add_call(self, qualname: str, line: int, initial_state=None) -> None:
        self.events.append(
            CallEvent(self.func_id(qualname), line,
                      freeze_mapping(initial_state) if initial_state else {})
        )

    def add_return(self, qualname: str) -> None:
        self.events.append(ReturnEvent(self.func_id(qualname)))

    # -- serialization ----------------------------------------------------

    def _header(self) -> bytes:
        out = bytearray(MAGIC)
        out += _u32(VERSION)
        out += _u32(1 if self.events else 0)  # flags: 1 = event table compressed
        out += struct.pack("<d", self.started)
        src = self.srcfile.encode("utf-8", "replace")
        out += _varint(len(src))
        out += src
        mods = "\n".join(self.modules).encode("utf-8", "replace")
        out += _varint(len(mods))
        out += mods
        out += self.strings.dump()
        return bytes(out)

    def _payload(self) -> bytes:
        body = bytearray()
        prev_line = {}  # (func_id, depth) -> last line, for line deltas
        depth = 0
        for ev in self.events:
            if ev.evtype == EV_CALL:
                depth += 1
                last = prev_line.get((ev.func_id, depth), 0)
                prev_line[(ev.func_id, depth)] = ev.line
                body += ev.encode(last, self.strings)
            elif ev.evtype == EV_RETURN:
                body += ev.encode(0, self.strings)
                depth -= 1
            else:
                last = prev_line.get((ev.func_id, depth), 0)
                prev_line[(ev.func_id, depth)] = ev.line
                body += ev.encode(last, self.strings)
        return zlib.compress(bytes(body), 6)

    def write(self, path: str) -> None:
        # The payload is built first because encoding interns variable names
        # into the string table, which is written as part of the header.
        payload = self._payload()
        header = self._header()
        with open(path, "wb") as fh:
            fh.write(header)
            fh.write(_u32(len(payload)))
            fh.write(payload)
            fh.write(MAGIC + b"END")


class TraceReader:
    """Reads a trace file back into an in-memory model for inspection."""

    TRAILER = MAGIC + b"END"

    def __init__(self, path: str):
        with open(path, "rb") as fh:
            data = fh.read()
        if not data.startswith(MAGIC):
            raise TraceFormatError("not a TraceWire trace file (bad magic)")
        # The writer has always appended a trailer, but the reader never
        # looked at it, so a trace truncated by even one byte was silently
        # accepted.  Verify the tail before parsing anything.
        if not data.endswith(self.TRAILER):
            raise TraceFormatError(
                "truncated trace: end marker missing "
                f"(file is {len(data)} bytes, expected to end with {self.TRAILER!r})"
            )
        pos = 4
        version, pos = _read_u32(data, pos)
        if version != VERSION:
            raise TraceFormatError(
                f"unsupported trace version {version} (this build reads version {VERSION})"
            )
        self.flags, pos = _read_u32(data, pos)
        _need(data, pos, 8)
        self.started = struct.unpack_from("<d", data, pos)[0]
        pos += 8
        size, pos = _read_varint(data, pos)
        _need(data, pos, size)
        self.srcfile = data[pos:pos + size].decode("utf-8", "replace")
        pos += size
        size, pos = _read_varint(data, pos)
        _need(data, pos, size)
        self.modules = data[pos:pos + size].decode("utf-8", "replace").split("\n") if size else []
        pos += size
        self.strings, pos = StringTable.load(data, pos)
        psize, pos = _read_u32(data, pos)
        body_end = len(data) - len(self.TRAILER)
        if pos + psize > body_end:
            raise TraceFormatError(
                f"truncated trace: payload claims {psize} bytes, "
                f"only {max(0, body_end - pos)} available"
            )
        try:
            payload = zlib.decompress(data[pos:pos + psize])
        except zlib.error as exc:
            raise TraceFormatError(f"corrupt trace payload: {exc}") from exc
        self._parse_events(payload)

    def _parse_events(self, payload: bytes) -> None:
        self.events = []
        prev_line = {}   # (func_id, depth) -> line
        depth = 0
        pos = 0
        nstrings = len(self.strings)
        while pos < len(payload):
            evtype = payload[pos]
            pos += 1
            func_id, pos = _read_u32(payload, pos)
            if func_id >= nstrings:
                raise TraceFormatError(
                    f"corrupt trace: event references function id {func_id}, "
                    f"but the string table holds {nstrings} entries"
                )
            if evtype == EV_RETURN:
                if depth == 0:
                    raise TraceFormatError("corrupt trace: return event with no matching call")
                self.events.append(ReturnEvent(func_id))
                depth -= 1
                continue
            if evtype == EV_CALL:
                depth += 1
            key = (func_id, depth)
            line_delta, pos = _read_svarint(payload, pos)
            line = prev_line.get(key, 0) + line_delta
            prev_line[key] = line
            nvars, pos = _read_varint(payload, pos)
            vars_ = {}
            for _ in range(nvars):
                name_id, pos = _read_varint(payload, pos)
                if name_id >= nstrings:
                    raise TraceFormatError(
                        f"corrupt trace: variable name id {name_id} out of range"
                    )
                value, pos = decode_value(payload, pos)
                vars_[self.strings.name(name_id)] = value
            if evtype == EV_CALL:
                self.events.append(CallEvent(func_id, line, vars_))
            elif evtype in (EV_LINE, EV_EXCEPTION):
                self.events.append(LineEvent(evtype, func_id, line, vars_))
            else:
                raise TraceFormatError(f"corrupt trace: unknown event type {evtype}")

    def func_name(self, func_id: int) -> str:
        return self.strings.name(func_id)

    def __len__(self) -> int:
        return len(self.events)
