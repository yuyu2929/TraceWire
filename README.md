# TraceWire

**Record what your Python program did. Inspect it after it died.**

TraceWire runs your program under instrumentation and writes the whole execution
to a compact `.trace` file — every control point, with the local variables as
they were *at that moment*. Then you open a timeline in your browser (or the
terminal) and click through it as if you were debugging: state, changed
variables, exceptions, source line.

No debugger attached. No `print()` archaeology. No reproduction required —
the crash is already recorded.

```
   your script          tracer            .trace file        viewer
  ┌───────────┐      ┌──────────┐      ┌────────────┐    ┌──────────────┐
  │  run once │─────▶│ settrace │─────▶│  delta +   │───▶│ HTML timeline│
  │           │      │ snapshots│      │  zlib      │    │ + CLI dump   │
  └───────────┘      └──────────┘      └────────────┘    └──────────────┘
```

```console
$ tracewire record bug.py -o bug.trace      # the program crashes
$ tracewire summary bug.trace
trace     bug.trace  (1.6 KB)
source    /home/me/bug.py
events    126   markers 80   exceptions 1
functions 3
  parse_config             41 markers   lines 12-38
  main                     12 markers   lines 41-49
  <module>                  6 markers   lines 1-52

$ tracewire html bug.trace -o bug.html      # click the red ring = the exception
```

## Why

Debuggers need the failure to happen *again*, under your eyes. The failures that
cost the most — the one in CI at 3am, the one a user reported, the one that only
reproduces on their machine — do not.

TraceWire takes the opposite approach: **record first, inspect later.** The
trace is a file. You can attach it to a bug report, open it on your laptop, and
see the exact state that led to the exception.

It is also a decent way to *understand* unfamiliar code: record it, click along
the timeline, and watch the variables move.

## Install

Python **3.8+**, standard library only — no dependencies at all.

```bash
pip install .            # provides the `tracewire` command
python -m tracewire --help   # or run it without installing
```

## Quick start

```bash
# 1. record an instrumented run
tracewire record examples/demo_program.py -o demo.trace

# 2. one-screen report
tracewire summary demo.trace

# 3. inspect the timeline in the terminal
tracewire dump demo.trace
tracewire dump demo.trace --index 20      # one control point, full state

# 4. click through it in a browser (single self-contained file)
tracewire html demo.trace -o demo.html
```

Group control points into named **sets** with Python predicates:

```bash
# every control point inside factorial
tracewire sets demo.trace --name recursion \
    --predicate "marker.function == 'factorial'"

# a set you can jump between with the ← / → buttons in the viewer
tracewire sets demo.trace --name big_total --important \
    --predicate "marker.has('total') and marker.get('total') > 10"
```

## Predicates

A predicate is any Python expression (with `marker` bound) or a function
definition — the function can have any name.

```python
# expression
marker.function == 'factorial'

# function
def any_name(marker):
    return marker.has('total') and marker.get('total') > 9
```

| attribute    | meaning                                              |
|--------------|------------------------------------------------------|
| `source`     | source file name                                     |
| `line`       | line number of the control point                     |
| `function`   | enclosing function name                              |
| `variables`  | full local-variable snapshot at this point           |
| `changed`    | variables that changed at this control point         |
| `types`      | `{name: type-name}` for the snapshot                 |
| `evtype`     | `1` = line control point, `4` = exception            |
| `has(name)` / `get(name, default)` | variable accessors           |

Containers are stored as their `repr()` (the format is dependency-free and
language-agnostic), so `marker.get('items')` is the string `'[1, 2]'`, not a
list. Compare as strings, or use `marker.changed` to test "did it move here".

If a predicate raises on a marker, that marker is treated as *not matching* and
the CLI tells you how many times it happened — a typo like `marker.total`
cannot silently produce an empty set.

## What gets recorded

| | |
|---|---|
| **Delta-compressed** | only *changed* variables are stored per control point; line numbers are deltas within a function; the event stream is zlib-compressed |
| **Recursion-safe** | frame state and line deltas are keyed by `(function, depth)`, so recursive calls never contaminate each other |
| **Frozen at the control point** | values are captured when the line executes, not when the trace is written — in-place mutations are visible, and every marker shows the value it actually had |
| **Selective** | `--include` / `--exclude` module globs; the tracer never records itself |
| **Crash-friendly** | a program that raises still produces a trace, and the exception is marked on the wire |
| **Bounded** | very large values are truncated at 4000 chars instead of exploding the file |

## Commands

```
tracewire record  <script> [args...] [-o out] [--include a,b] [--exclude c,d] [--max-locals N]
tracewire dump    <trace> [--index N]
tracewire summary <trace> [--json]
tracewire sets    <trace> --name N --predicate P [--hidden] [--important]
tracewire sets    <trace> --list
tracewire html    <trace> [-o out.html] [--title T]
```

Exit codes: `0` success, `2` usage error, otherwise the traced program's own
exit status. `record` writes the trace even when the program fails, so it is
safe to use as `tracewire record test.py -o t.trace || upload t.trace`.

## Trace file format (v2)

```
MAGIC "TWTR" | u32 version=2 | u32 flags | f64 start-time
| varstr source-file | varstr modules | string table
| u32 payload-size | zlib(event stream) | "TWTREND"
```

Event stream: `type u8`, `func-id u32`, then per event — signed-varint line
delta (except return), varint count + `(name-id, value)` pairs for the changed
variables. Values use a 7-tag type discriminator; arbitrary objects fall back to
`repr()`. Function **and** variable names are interned in the string table and
referenced by id. Full details in [`tracewire/core.py`](tracewire/core.py) and
[`docs/DESIGN_NOTES.md`](docs/DESIGN_NOTES.md).

The reader verifies the trailing `TWTREND` marker, the payload length and every
offset it reads: a truncated or corrupt trace raises `TraceFormatError` instead
of silently returning partial data. Version 1 traces (variable names inline)
are rejected with an explicit message.

## Limits — read this before trusting a trace

- **One process, one run.** `sys.settrace` sees the current interpreter only;
  threads other than the traced one, subprocesses and C extensions are invisible.
- **Containers are repr() snapshots**, truncated at 4000 chars. Deeply nested
  or huge structures are not faithfully reconstructable.
- **Slower than running normally.** `settrace` costs roughly one Python-level
  callback per line; use `--include` to narrow the scope on big programs.
- **Everything is held in memory** while recording and while reading. A million
  control points is a lot of RAM; streaming is on the roadmap.
- **Expressions, columns and object identity** are not captured — `col` is
  always 0 and variables are compared by `repr()`, not by `is`.

## Development

```bash
python tests/test_core.py         # format codec round-trips
python tests/test_e2e.py          # record -> model -> sets -> html
python tests/test_regressions.py  # 22 regression tests for fixed defects
```

or, if you prefer pytest: `pytest tests/`.

## License

TraceWire is **dual-licensed**:

- **AGPL-3.0-or-later** for individuals, study and internal company use — see [LICENSE](LICENSE).
- **Commercial license** for embedding it in a product, shipping it in an
  appliance, or offering it as a hosted debugging service — see
  [COMMERCIAL.md](COMMERCIAL.md).

Copyright © TraceWire Contributors.
