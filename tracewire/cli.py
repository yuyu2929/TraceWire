"""tracewire.cli -- command line interface.

Commands
--------
    record   <script> [args...]      run a program under instrumentation
    dump     <trace> [--index N]     print the timeline / inspect a marker
    summary  <trace> [--json]        one-screen report (CI friendly)
    sets     <trace> <name> <pred>   create a marker set from a predicate
    sets     <trace> --list          list memoized sets
    html     <trace> -o out.html     render an interactive timeline viewer
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, core, markers
from .tracer import record as _record, ProgramFailed

EXIT_OK = 0
EXIT_USAGE = 2
# A traced program that raised/exited non-zero is reported with its own code
# when it is a valid small integer, otherwise with this one.
EXIT_PROGRAM_FAILED = 1


def _default_setfile(trace_path: str) -> str:
    return trace_path + ".sets.json"


def _split_globs(text: str):
    return [part.strip() for part in text.split(",") if part.strip()]


def _load(trace_path):
    """Reader + model + set store, with the memo warnings surfaced once."""
    reader = core.TraceReader(trace_path)
    model = markers.TraceModel(reader)
    store = markers.SetStore(_default_setfile(trace_path))
    return reader, model, store


def _warn_memo(store):
    if store.load_error:
        print(f"warning: {store.path}: {store.load_error}", file=sys.stderr)


def _emit_set_warnings(store):
    for s in store.sets.values():
        if s.skipped:
            print(f"warning: set '{s.name}': predicate raised on {s.skipped} marker(s) "
                  f"and was treated as false", file=sys.stderr)


def cmd_record(args):
    if not args.script:
        print("error: record requires a script", file=sys.stderr)
        return EXIT_USAGE
    out = args.output or (args.script + ".trace")
    include = _split_globs(args.include) or ("*",)
    exclude = _split_globs(args.exclude)
    try:
        writer = _record(
            args.script,
            out,
            include=include,
            exclude=exclude,
            argv=args.args,
            max_locals=args.max_locals,
            raise_on_error=True,
        )
    except ProgramFailed as exc:
        # The trace on disk is the interesting artifact for a crash, so keep
        # it and still fail the command.
        print(f"tracewire: traced program failed ({exc.error!r}); "
              f"trace written to {out}", file=sys.stderr)
        return EXIT_PROGRAM_FAILED if exc.exit_code in (0, None) else int(exc.exit_code)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"recorded {len(writer.events)} control-point events -> {out}")
    return EXIT_OK


def cmd_dump(args):
    reader, model, store = _load(args.trace)
    _warn_memo(store)
    if args.index is not None:
        if not 0 <= args.index < len(model.markers):
            print(f"error: marker index {args.index} out of range "
                  f"(trace has {len(model.markers)} markers)", file=sys.stderr)
            return EXIT_USAGE
        m = model.markers[args.index]
        kind = "exception" if m.evtype == core.EV_EXCEPTION else "line"
        print(f"marker #{m.index}  {os.path.basename(m.source)}:{m.line}  in {m.function}"
              f"  ({kind})")
        print("-- variables --")
        if not m.variables:
            print("  (no locals)")
        for k, v in m.variables.items():
            flag = " *changed*" if k in m.changed else ""
            print(f"  {k} = {v!r}{flag}")
        return EXIT_OK
    print(f"trace of {model.source}  ({len(model.markers)} markers)")
    print("  event | function:line | changed vars | sets")
    for m in model.markers:
        tag = "E" if m.evtype == core.EV_EXCEPTION else " "
        sets = [name for name, s in store.sets.items() if s.visible and s.contains(m)]
        sets_txt = (" ".join("[" + s + "]" for s in sets)) if sets else ""
        print(f"  {tag} #{m.index:<5} {m.function}:{m.line} "
              f"{len(m.changed):>2} vars  {sets_txt}")
    for name, s in store.sets.items():
        if s.important:
            sel = s.select(model.markers)
            if sel:
                print(f"  [important] {name}: {len(sel)} markers, "
                      f"first #{sel[0].index}, last #{sel[-1].index}")
            else:
                print(f"  [important] {name}: 0 markers")
    _emit_set_warnings(store)
    return EXIT_OK


def cmd_summary(args):
    reader, model, store = _load(args.trace)
    _warn_memo(store)
    per_func = {}
    exceptions = 0
    for m in model.markers:
        entry = per_func.setdefault(m.function, {"count": 0, "lines": set()})
        entry["count"] += 1
        entry["lines"].add(m.line)
        if m.evtype == core.EV_EXCEPTION:
            exceptions += 1
    set_rows = []
    for name, s in store.sets.items():
        set_rows.append({
            "name": name,
            "matched": len(s.select(model.markers)),
            "visible": s.visible,
            "important": s.important,
            "errors": s.skipped,
        })
    data = {
        "trace": os.path.abspath(args.trace),
        "source": model.source,
        "events": len(reader.events),
        "markers": len(model.markers),
        "bytes": os.path.getsize(args.trace),
        "exceptions": exceptions,
        "functions": [
            {"name": name,
             "markers": info["count"],
             "lines": [min(info["lines"]), max(info["lines"])]}
            for name, info in sorted(per_func.items(),
                                     key=lambda kv: (-kv[1]["count"], kv[0]))
        ],
        "sets": set_rows,
    }
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return EXIT_OK
    size = data["bytes"]
    human = f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"
    print(f"trace     {args.trace}  ({human})")
    print(f"source    {model.source}")
    print(f"events    {data['events']}   markers {data['markers']}   "
          f"exceptions {exceptions}")
    print(f"functions {len(per_func)}")
    for row in data["functions"]:
        lo, hi = row["lines"]
        print(f"  {row['name']:<24} {row['markers']:>5} markers   lines {lo}-{hi}")
    if set_rows:
        print("sets")
        for row in set_rows:
            flags = []
            if not row["visible"]:
                flags.append("hidden")
            if row["important"]:
                flags.append("important")
            if row["errors"]:
                flags.append(f"{row['errors']} predicate errors")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(f"  {row['name']:<24} {row['matched']:>5} markers{suffix}")
    return EXIT_OK


def cmd_sets(args):
    reader, model, store = _load(args.trace)
    _warn_memo(store)
    if args.list:
        for name, s in store.sets.items():
            n = len(s.select(model.markers))
            print(f"{name}: {n} markers, visible={s.visible}, "
                  f"important={s.important}, pred={s.predicate_src or '(all)'}")
            if s.skipped:
                print(f"  warning: predicate raised on {s.skipped} marker(s)")
        return EXIT_OK
    if not args.name or not args.predicate:
        print("error: sets requires --name and a predicate", file=sys.stderr)
        return EXIT_USAGE
    try:
        ms = markers.MarkerSet(args.name, predicate_src=args.predicate,
                               visible=not args.hidden, important=args.important)
    except Exception as exc:
        print(f"error: bad predicate: {exc}", file=sys.stderr)
        return EXIT_USAGE
    selected = ms.select(model.markers)
    store.add(ms)
    print(f"set '{args.name}': {len(selected)}/{len(model.markers)} markers matched, saved to "
          f"{_default_setfile(args.trace)}")
    if ms.skipped:
        # Almost always a typo in the predicate (e.g. `marker.total` instead of
        # `marker.get('total')`); say so instead of reporting a clean 0 match.
        print(f"warning: the predicate raised on {ms.skipped} marker(s) - "
              f"it was treated as false there. Check attribute names.",
              file=sys.stderr)
    return EXIT_OK


def cmd_html(args):
    reader, model, store = _load(args.trace)
    _warn_memo(store)
    from .html_viewer import render_html
    out = args.output or (os.path.splitext(args.trace)[0] + ".html")
    render_html(model, store, out, title=args.title)
    print(f"wrote {out}")
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tracewire", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"tracewire {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_record = sub.add_parser("record", help="run a program under instrumentation")
    p_record.add_argument("script")
    p_record.add_argument("args", nargs="*")
    p_record.add_argument("-o", "--output", help="trace output path")
    p_record.add_argument("--include", default="*", help="comma-separated module globs to trace")
    p_record.add_argument("--exclude", default="tracewire", help="comma-separated module globs to skip")
    p_record.add_argument("--max-locals", type=int, default=None)
    p_record.set_defaults(func=cmd_record)

    p_dump = sub.add_parser("dump", help="print the trace timeline")
    p_dump.add_argument("trace")
    p_dump.add_argument("--index", type=int, default=None, help="inspect a single marker")
    p_dump.set_defaults(func=cmd_dump)

    p_summary = sub.add_parser("summary", help="one-screen report of a trace")
    p_summary.add_argument("trace")
    p_summary.add_argument("--json", action="store_true", help="machine-readable output")
    p_summary.set_defaults(func=cmd_summary)

    p_sets = sub.add_parser("sets", help="build marker sets from predicates (memoized)")
    p_sets.add_argument("trace")
    p_sets.add_argument("--name")
    p_sets.add_argument("--predicate", help="python expression/function over `marker`")
    p_sets.add_argument("--hidden", action="store_true", help="set visible=False")
    p_sets.add_argument("--important", action="store_true", help="set important=True")
    p_sets.add_argument("--list", action="store_true", help="list memoized sets")
    p_sets.set_defaults(func=cmd_sets)

    p_html = sub.add_parser("html", help="render an interactive HTML timeline viewer")
    p_html.add_argument("trace")
    p_html.add_argument("-o", "--output")
    p_html.add_argument("--title")
    p_html.set_defaults(func=cmd_html)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK
    try:
        return args.func(args)
    except core.TraceFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PROGRAM_FAILED
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # downstream closed the pipe (e.g. `tracewire dump x | head`)
        sys.exit(0)
