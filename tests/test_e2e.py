"""Tests for tracewire.tracer + tracewire.markers end to end."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracewire import core, markers
from tracewire.tracer import record


def _run(script, out):
    writer = record(os.path.join(ROOT, "examples", script), out)
    return writer


def test_record_produces_events():
    fd, out = tempfile.mkstemp(suffix=".trace")
    os.close(fd)
    os.remove(out)
    try:
        writer = _run("demo_program.py", out)
        assert len(writer.events) > 50, "expected many control points"
        reader = core.TraceReader(out)
        assert len(reader.events) == len(writer.events)
    finally:
        if os.path.exists(out):
            os.remove(out)


def test_trace_model_reconstructs_state():
    fd, out = tempfile.mkstemp(suffix=".trace")
    os.close(fd)
    os.remove(out)
    try:
        _run("demo_program.py", out)
        reader = core.TraceReader(out)
        model = markers.TraceModel(reader)
        assert len(model.markers) == len(reader.events) - sum(
            1 for e in reader.events if e.evtype in (2, 3)), "call/return must not become markers"
        # every marker must have a function and a line
        for m in model.markers:
            assert m.function and m.line >= 1
        # the accumulate function must appear with growing `total`
        totals = [m.variables["total"] for m in model.markers
                  if m.function == "accumulate" and m.has("total")]
        assert totals == sorted(totals), "total should grow monotonically"
    finally:
        if os.path.exists(out):
            os.remove(out)


def test_predicate_sets():
    fd, out = tempfile.mkstemp(suffix=".trace")
    os.close(fd)
    os.remove(out)
    try:
        _run("demo_program.py", out)
        reader = core.TraceReader(out)
        model = markers.TraceModel(reader)

        recursion = markers.MarkerSet("recursion",
                                      predicate_src="marker.function == 'factorial'")
        rec = recursion.select(model.markers)
        assert len(rec) > 0 and all(m.function == "factorial" for m in rec)

        big = markers.MarkerSet("big",
                                predicate_src="marker.has('total') and marker.get('total') > 6")
        sel = big.select(model.markers)
        assert all(m.get("total") > 6 for m in sel)

        funcset = markers.MarkerSet("deep", predicate_src=(
            "def pred(marker):\n    return marker.function == 'factorial' and marker.get('n', 0) <= 2\n"))
        assert all(m.get("n", 99) <= 2 for m in funcset.select(model.markers))
    finally:
        if os.path.exists(out):
            os.remove(out)


def test_set_store_memoization():
    fd, out = tempfile.mkstemp(suffix=".trace")
    os.close(fd)
    os.remove(out)
    try:
        _run("demo_program.py", out)
        store_path = out + ".sets.json"
        store = markers.SetStore(store_path)
        store.add(markers.MarkerSet("all"))
        store.save()
        store2 = markers.SetStore(store_path)
        assert "all" in store2.sets
        assert store2.get("all").visible is True
        os.remove(store_path)
    finally:
        if os.path.exists(out):
            os.remove(out)
        if os.path.exists(out + ".sets.json"):
            os.remove(out + ".sets.json")


def test_html_viewer_renders():
    fd, out = tempfile.mkstemp(suffix=".trace")
    os.close(fd)
    os.remove(out)
    html_path = out + ".html"
    try:
        _run("demo_program.py", out)
        reader = core.TraceReader(out)
        model = markers.TraceModel(reader)
        store = markers.SetStore(out + ".sets.json")
        store.add(markers.MarkerSet("recursion",
                                    predicate_src="marker.function == 'factorial'"))
        from tracewire.html_viewer import render_html
        render_html(model, store, html_path)
        with open(html_path, encoding="utf-8") as fh:
            page = fh.read()
        assert "TraceWire" in page
        assert '"markers"' in page
        assert "recursion" in page
    finally:
        for p in (out, html_path, out + ".sets.json"):
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    for fn in sorted(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
            print(f"ok  {fn}")
    print("all end-to-end tests passed")
