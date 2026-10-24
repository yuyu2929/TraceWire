"""Regression tests for the 0.2.0 correctness and packaging fixes.

Each test corresponds to a defect that was reproduced before being fixed.
Run directly (``python tests/test_regressions.py``) or under pytest.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracewire import core, markers
from tracewire.tracer import record, ProgramFailed, Tracer
from tracewire.html_viewer import render_html

TMP = tempfile.mkdtemp(prefix="tracewire-reg-")
_counter = [0]


def _script(body):
    _counter[0] += 1
    path = os.path.join(TMP, f"s{_counter[0]}.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _record(body):
    path = _script(body)
    out = path + ".trace"
    writer = record(path, out)
    return writer, out, core.TraceReader(out)


def _cli(*args):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "tracewire", *args],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


# --------------------------------------------------------------- fidelity

def test_values_are_frozen_at_the_control_point():
    """A mutated list must show its value *at that point*, not its final value."""
    _, out, reader = _record(
        "def main():\n"
        "    items = []\n"
        "    items.append(1)\n"
        "    items.append(2)\n"
        "    items.append(3)\n"
        "    return len(items)\n"
        "\n"
        "main()\n"
    )
    model = markers.TraceModel(reader)
    seen = [m.variables["items"] for m in model.markers if "items" in m.variables]
    assert seen, "items never recorded"
    assert len(set(seen)) >= 3, f"in-place mutations were invisible: {seen}"
    assert seen[0] in ("[]", "[]"), seen
    assert seen[-1] == "[1, 2, 3]", seen[-1]


def test_snapshot_does_not_alias_live_objects():
    """The stored snapshot must not be changed by later mutation of the object."""
    writer = core.TraceWriter("x.py")
    writer.add_call("main", 1)
    items = [1]
    writer.add_line("main", 2, {"items": items})
    items.append(2)          # mutate after the event was recorded
    writer.add_line("main", 3, {"items": items})
    writer.add_return("main")
    path = os.path.join(TMP, "alias.trace")
    writer.write(path)
    events = [e for e in core.TraceReader(path).events if getattr(e, "vars", None)]
    assert events[0].vars["items"] == "[1]", events[0].vars
    assert events[1].vars["items"] == "[1, 2]", events[1].vars


def test_huge_values_are_truncated():
    from tracewire.tracer import freeze_value, MAX_REPR
    frozen = freeze_value(list(range(20000)))
    assert isinstance(frozen, str)
    assert len(frozen) < MAX_REPR + 120, len(frozen)
    assert "truncated" in frozen


# --------------------------------------------------------------- crash path

def test_crash_does_not_pollute_the_trace():
    """Printing a traceback used to be recorded: 32k junk events for 4 lines."""
    writer, _, reader = _record(
        "def f():\n"
        "    raise ValueError('boom')\n"
        "\n"
        "f()\n"
    )
    assert len(reader.events) < 30, f"trace polluted with {len(reader.events)} events"
    names = {writer.strings.name(i) for i in range(len(writer.strings))}
    for junk in ("TextWrapper", "Tokenizer", "escape", "_compile"):
        assert junk not in names, f"traceback internals leaked into the trace: {junk}"


def test_record_exposes_program_failure():
    path = _script("raise SystemExit(3)\n")
    out = path + ".trace"
    writer = record(path, out)
    assert writer.exit_code == 3
    assert writer.error is not None
    assert os.path.exists(out), "the trace must still be written for a crash"

    try:
        record(path, out, raise_on_error=True)
    except ProgramFailed as exc:
        assert exc.exit_code == 3
    else:
        raise AssertionError("raise_on_error did not raise")


def test_cli_record_fails_when_the_program_fails():
    path = _script("raise ValueError('nope')\n")
    proc = _cli("record", path, "-o", path + ".trace")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert os.path.exists(path + ".trace")


# --------------------------------------------------------------- trace format

def test_truncated_trace_is_rejected():
    _, out, _ = _record("x = 1\n")
    with open(out, "rb") as fh:
        data = fh.read()
    for blob, label in [(data[:-1], "one byte short"), (data[: len(data) // 2], "half")]:
        bad = os.path.join(TMP, "bad.trace")
        with open(bad, "wb") as fh:
            fh.write(blob)
        try:
            core.TraceReader(bad)
        except core.TraceFormatError as exc:
            assert "truncat" in str(exc) or "corrupt" in str(exc), str(exc)
        else:
            raise AssertionError(f"{label} trace was accepted")


def test_version_1_trace_is_rejected_with_a_clear_message():
    path = os.path.join(TMP, "v1.trace")
    with open(path, "wb") as fh:
        # magic + version 1 + a valid trailer: the trailer check passes, so the
        # version check is what must fire.
        fh.write(core.MAGIC + (1).to_bytes(4, "little") + core.MAGIC + b"END")
    try:
        core.TraceReader(path)
    except core.TraceFormatError as exc:
        assert "version" in str(exc), str(exc)
    else:
        raise AssertionError("v1 trace accepted by a v2 reader")


def test_variable_names_are_interned():
    """v2 stores a name id instead of repeating the name on every event."""
    writer = core.TraceWriter("x.py")
    writer.add_call("main", 1, {"a_very_long_variable_name": 0})
    for i in range(50):
        writer.add_line("main", 2, {"a_very_long_variable_name": i})
    writer.add_return("main")
    path = os.path.join(TMP, "interned.trace")
    writer.write(path)
    name = "a_very_long_variable_name"
    raw = open(path, "rb").read()
    assert raw.count(name.encode()) <= 1, "variable name was written inline more than once"
    reader = core.TraceReader(path)
    events = [e for e in reader.events if getattr(e, "vars", None)]
    assert events[-1].vars[name] == 49


def test_string_table_interning_is_indexed():
    tbl = core.StringTable()
    assert tbl.intern("a") == 0
    assert tbl.intern("b") == 1
    assert tbl.intern("a") == 0
    assert len(tbl) == 2
    assert isinstance(tbl._index, dict)


# --------------------------------------------------------------- predicates

def test_predicate_function_can_have_any_name():
    pred = markers.make_predicate("def my_rule(m):\n    return m.line > 3\n")
    class M:
        line = 5
    assert pred(M()) is True


def test_predicate_without_a_callable_is_a_clear_error():
    # Several callables with no `pred`: refuse instead of silently picking one.
    try:
        markers.make_predicate("def a(m):\n    return True\ndef b(m):\n    return False\n")
    except ValueError as exc:
        assert "pred" in str(exc), str(exc)
    else:
        raise AssertionError("ambiguous predicate was accepted")
    # A block that defines nothing callable cannot be produced by starting
    # with "def", so a non-def snippet is treated as an expression; an invalid
    # one must fail loudly rather than yield a predicate that never matches.
    try:
        markers.make_predicate("x = 1")
    except SyntaxError:
        pass
    else:
        raise AssertionError("an invalid expression predicate was accepted")


def test_predicate_errors_are_counted_not_swallowed():
    _, _, reader = _record("x = 1\ny = 2\n")
    model = markers.TraceModel(reader)
    ms = markers.MarkerSet("typo", predicate_src="marker.total > 10")
    assert ms.select(model.markers) == []
    assert ms.skipped > 0, "the predicate raised but nothing was reported"


def test_corrupt_set_store_degrades_to_empty():
    path = os.path.join(TMP, "corrupt.sets.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    store = markers.SetStore(path)
    assert store.sets == {}
    assert store.load_error, "the corruption should be reported, not hidden"
    store.add(markers.MarkerSet("recovered"))
    assert "recovered" in markers.SetStore(path).sets


# --------------------------------------------------------------- viewer

def test_html_renders_bytes_values():
    _, out, reader = _record(
        "def main():\n"
        "    payload = b'\\x00\\x01\\xff'\n"
        "    return payload\n"
        "\n"
        "main()\n"
    )
    model = markers.TraceModel(reader)
    html_path = os.path.join(TMP, "bytes.html")
    render_html(model, markers.SetStore(out + ".sets.json"), html_path)
    assert os.path.getsize(html_path) > 1000


def test_html_payload_is_parseable_json():
    _, out, reader = _record("x = 1\ny = 'two'\n")
    model = markers.TraceModel(reader)
    html_path = os.path.join(TMP, "payload.html")
    render_html(model, markers.SetStore(out + ".sets.json"), html_path)
    page = open(html_path, encoding="utf-8").read()
    start = page.index("const DATA = ") + len("const DATA = ")
    end = page.index(";\n", start)
    data = json.loads(page[start:end].replace("<\\/", "</"))
    assert data["model"]["markers"]


# --------------------------------------------------------------- cli

def test_dump_rejects_out_of_range_index():
    _, out, _ = _record("x = 1\n")
    proc = _cli("dump", out, "--index", "9999")
    assert proc.returncode == 2
    assert "out of range" in proc.stderr


def test_python_dash_m_tracewire_works():
    proc = _cli("--version")
    assert proc.returncode == 0, proc.stderr
    assert "tracewire" in proc.stdout


def test_summary_json_is_machine_readable():
    _, out, _ = _record("def f():\n    return 1\n\nf()\n")
    proc = _cli("summary", out, "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["markers"] > 0
    assert any(f["name"] == "f" for f in data["functions"])


# --------------------------------------------------------------- filtering

def test_exclude_is_case_sensitive_so_a_TraceWire_checkout_is_traced():
    """fnmatch() lowercases on Windows; 'tracewire' must not match 'TraceWire'."""
    assert Tracer._matches("tracewire", "C:/work/TraceWire/examples/demo.py") is False
    assert Tracer._matches("tracewire", "C:/work/tracewire/core.py") is True
    assert Tracer._matches("*.py", "C:/work/TraceWire/examples/demo.py") is True


def test_tracer_never_traces_its_own_package():
    # The qualified name of a tracewire frame resolves inside the package dir.
    from tracewire import core as core_mod
    code = core_mod._u32.__code__
    tracer = Tracer(core.TraceWriter("x.py"), include=("*",), exclude=())
    assert tracer._wanted(code) is False


def test_include_filter_still_selects_a_single_file():
    tracer = Tracer(core.TraceWriter("x.py"), include=["demo_program.py"], exclude=())
    code = compile("x = 1\n", os.path.join(TMP, "demo_program.py"), "exec")
    assert tracer._wanted(code) is True
    other = compile("x = 1\n", os.path.join(TMP, "other.py"), "exec")
    assert tracer._wanted(other) is False


if __name__ == "__main__":
    failures = 0
    for name in sorted(globals()):
        if not name.startswith("test_"):
            continue
        try:
            globals()[name]()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {name}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
