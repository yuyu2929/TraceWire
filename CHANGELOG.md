# Changelog

All notable changes to TraceWire are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project adheres to
semantic versioning. The trace format has its own version (`VERSION` in
`tracewire/core.py`); a breaking format change bumps the major version.

## [0.2.0] - 2026-10-25

Correctness and packaging release. The headline fix: a trace now records the
value a variable had **at the control point**, not the value it ended up with.

### Fixed

- **Snapshots aliased live objects.** Values were encoded in `write()`, i.e.
  after the program had finished, so every marker showed the final state of
  any mutable variable and in-place mutations (`list.append`, `dict[k] = v`)
  were invisible. Values are now frozen when the event is recorded
  (`core.freeze_value`), applied both by the tracer and by `TraceWriter`, so
  direct API users get the same guarantee.
- **Crash traces were polluted with traceback internals.** `traceback.print_exc()`
  ran before `tracer.stop()`, recording the traceback machinery itself: a
  four-line script that raised produced **32,562 events across 149 functions**
  (`TextWrapper`, `Tokenizer`, `linecache`, ...) and buried the user's own
  frames. Tracing now stops first; the same script produces **9 events**.
- **Truncated traces were accepted silently.** The writer had always appended a
  `TWTREND` trailer and the docstring claimed readers verified it, but the
  reader never looked: a file truncated by one byte parsed "successfully", and
  one truncated by half raised a bare `zlib.error`. The reader now checks the
  trailer, the payload length and every read, raising `TraceFormatError`.
- **`record()` swallowed the program's failure.** The CLI exited 0 even when
  the traced program raised, so CI could not detect it. `writer.error` and
  `writer.exit_code` are now populated, `raise_on_error=True` propagates a
  `ProgramFailed`, and `tracewire record` exits with the program's status.
- **Variable names were written inline on every event** despite the README
  claiming they were interned. Format **v2** interns them into the string table
  (name id instead of the name). v1 files are rejected with an explicit message.
- **`StringTable.intern` was O(n)** (`list.index`), called on every event:
  0.23 s to intern 8,000 unique names, 10.2 µs per hot lookup. It now uses a
  dict index.
- **The HTML viewer crashed on `bytes` values** with
  `TypeError: Object of type bytes is not JSON serializable`. Any program that
  touched a byte string could not be rendered.
- **Predicate functions had to be named `pred`**; any other name raised a bare
  `KeyError`. Any single callable defined by the snippet is now accepted.
- **Predicate errors were swallowed**, so a typo such as `marker.total` (which
  the bundled example used) silently produced an empty set that looked valid.
  `MarkerSet.skipped` counts them and the CLI warns.
- **A corrupt `<trace>.sets.json` broke every subsequent command.** It now
  degrades to an empty store with a warning, and is written atomically.
- **`dump --index` out of range** raised a raw `IndexError`; it now reports the
  valid range and exits 2.
- **`--exclude` never matched a bare module name** (paths look like
  `.../tracewire/core.py`, not `tracewire`). It now matches directory
  components, case-sensitively — `fnmatch()` lowercases on Windows, which made
  a `TraceWire` checkout match the default `tracewire` exclude and record
  nothing at all.
- The tracer no longer traces its own package, matched by real path rather than
  by a glob.

### Added

- `tracewire summary <trace> [--json]`: one-screen report (functions, markers,
  line ranges, exception count, set membership) for CI and PR comments.
- `python -m tracewire` (a `__main__.py` was missing).
- `TraceFormatError` with actionable messages for truncated, corrupt and
  version-mismatched files.
- 22 regression tests (`tests/test_regressions.py`) covering every defect above.
- `MAX_REPR = 4000`: oversized values are truncated with their original length
  noted instead of bloating the trace.

### Changed

- **License: MIT → AGPL-3.0-or-later, with a commercial option.** MIT allowed
  any vendor to embed the tracer and viewer in a closed product for free. See
  [COMMERCIAL.md](COMMERCIAL.md).
- `record()` restores `sys.argv` and `sys.path`; `python -m tracewire` is the
  documented invocation.
- CLI exit codes: `0` ok, `2` usage error, otherwise the traced program's status.

### Known limits

- Containers are still recorded as `repr()` strings (format is dependency-free),
  so predicates compare them as strings.
- Everything is held in memory while recording and replaying.

## [0.1.0] - 2026-10-21

First release: the complete TraceWire platform — delta-compressed trace format,
`sys.settrace` recorder, marker sets with Python predicates, terminal timeline
and a self-contained interactive HTML viewer.

[0.2.0]: https://github.com/yuyu2929/TraceWire/releases/tag/v0.2.0
[0.1.0]: https://github.com/yuyu2929/TraceWire/releases/tag/v0.1.0
