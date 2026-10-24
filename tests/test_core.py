"""Tests for tracewire.core: trace file format round-trips and delta codec."""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracewire import core


def _roundtrip(events):
    writer = core.TraceWriter("fake.py", modules=["*"])
    for ev in events:
        if ev[0] == "line":
            writer.add_line(ev[1], ev[2], ev[3])
        elif ev[0] == "call":
            writer.add_call(ev[1], ev[2])
        elif ev[0] == "return":
            writer.add_return(ev[1])
    fd, path = tempfile.mkstemp(suffix=".trace")
    os.close(fd)
    try:
        writer.write(path)
        reader = core.TraceReader(path)
        return reader
    finally:
        os.remove(path)


def test_magic_version():
    writer = core.TraceWriter("x.py")
    fd, path = tempfile.mkstemp(suffix=".trace")
    os.close(fd)
    try:
        writer.write(path)
        with open(path, "rb") as fh:
            head = fh.read(8)
        assert head.startswith(core.MAGIC), "magic missing"
    finally:
        os.remove(path)


def test_roundtrip_simple():
    reader = _roundtrip([
        ("call", "main", 1),
        ("line", "main", 1, {"a": 1, "b": "x"}),
        ("line", "main", 2, {"a": 2}),           # delta: only a changed
        ("return", "main"),
    ])
    assert reader.func_name(0) == "main"
    assert len(reader.events) == 4
    # line events are 1 and 2
    lines = [e for e in reader.events if e.evtype == core.EV_LINE]
    assert [e.line for e in lines] == [1, 2]
    assert lines[1].vars == {"a": 2}


def test_value_codec():
    samples = [None, True, False, 0, -42, 10 ** 20, 3.14159, "héllo",
               b"\x00\x01", [1, 2], {"k": (1, 2)}]
    for v in samples:
        enc = core.encode_value(v)
        dec, pos = core.decode_value(enc, 0)
        assert pos == len(enc), f"trailing bytes for {v!r}"
        if isinstance(v, float):
            assert math.isclose(dec, v)
        elif isinstance(v, (list, dict)):
            assert dec == repr(v)  # falls back to repr
        else:
            assert dec == v, f"{v!r} -> {dec!r}"


def test_delta_saves_space():
    """A long loop over one variable must not duplicate unchanged vars."""
    writer = core.TraceWriter("loop.py", modules=["*"])
    writer.add_call("main", 1)
    for i in range(200):
        writer.add_line("main", 5, {"i": i, "const": 99} if i == 0 else {"i": i})
    writer.add_return("main")
    fd, path = tempfile.mkstemp(suffix=".trace")
    os.close(fd)
    try:
        writer.write(path)
        size = os.path.getsize(path)
        # 200 events each with a small delta should stay compact (< 8 KB)
        assert size < 8 * 1024, f"trace too large: {size} bytes"
        reader = core.TraceReader(path)
        lines = [e for e in reader.events if e.evtype == core.EV_LINE]
        assert len(lines) == 200
        assert lines[199].vars["i"] == 199
    finally:
        os.remove(path)


if __name__ == "__main__":
    for fn in sorted(globals()):
        if fn.startswith("test_"):
            fn_ = globals()[fn]
            fn_()
            print(f"ok  {fn}")
    print("all core tests passed")
