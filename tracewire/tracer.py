"""tracewire.tracer -- instrumentation tracer.

Uses Python's :func:`sys.settrace` hook to walk an execution and record
control points.  Only the modules matching the include filter are
instrumented ("only the needed modules and functions are instrumented" --
by default just the module of the program being run), which keeps both
the overhead and the trace size focused.

At every line event (a control point) the tracer snapshots the *changed*
local variables of the frame relative to the previous snapshot in that
same frame, together with call / return / exception markers, and feeds
them into a :class:`tracewire.core.TraceWriter`.
"""

from __future__ import annotations

import os
import sys
import fnmatch
import traceback as _traceback

from . import core
# Values are captured *at the control point*, not when the trace is written:
# storing the live object would mean every control point shows the value the
# variable ends up with after the program finishes, and in-place mutations
# would be invisible.  The helper lives in core so that any producer of events
# (not just this tracer) gets the same guarantee.
from .core import MAX_REPR, freeze_value  # noqa: F401  (MAX_REPR re-exported)

# The tracer must never trace itself.  Doing this by path (rather than by the
# default exclude glob "tracewire") is precise and immune to the checkout
# directory being called "TraceWire": fnmatch is case-insensitive on Windows,
# so a directory-component match would exclude the user's whole project.
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))


class ProgramFailed(RuntimeError):
    """The traced program raised or exited non-zero."""

    def __init__(self, error, exit_code):
        super().__init__(f"traced program failed: {error!r}")
        self.error = error
        self.exit_code = exit_code


class Tracer:
    """Instruments the current interpreter and records into a TraceWriter."""

    def __init__(self, writer, include=("*",), exclude=("tracewire",), max_locals=None):
        """
        include : module name globs that are eligible for instrumentation.
        exclude : module name globs that are never instrumented.
        max_locals: cap on the number of locals captured per snapshot
                    (guards against pathological frames).
        """
        self.writer = writer
        self.include = list(include) or ["*"]
        self.exclude = list(exclude) or []
        self.max_locals = max_locals
        self._snapshots = {}   # id(frame) -> {name: frozen value}
        self._wanted_cache = {}   # filename -> bool (called on every call event)
        self._event_count = 0

    # -- filtering ---------------------------------------------------------

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        # fnmatchcase: case-sensitive everywhere.  fnmatch() lowercases on
        # Windows, which made "tracewire" match a "TraceWire" checkout.
        if fnmatch.fnmatchcase(path, pattern):
            return True
        if fnmatch.fnmatchcase(os.path.basename(path), pattern):
            return True
        # A bare name like the default "tracewire" should match a *directory*
        # component (module paths look like .../tracewire/core.py).
        parts = path.replace("\\", "/").split("/")
        return any(fnmatch.fnmatchcase(part, pattern) for part in parts[:-1])

    def _wanted(self, code) -> bool:
        module = code.co_filename
        cached = self._wanted_cache.get(module)
        if cached is not None:
            return cached
        verdict = self._decide(module)
        self._wanted_cache[module] = verdict
        return verdict

    def _decide(self, module) -> bool:
        if not module or module.startswith("<"):   # interactive, builtins
            return False
        absolute = os.path.abspath(module)
        if absolute == _PKG_DIR or absolute.startswith(_PKG_DIR + os.sep):
            return False                            # never trace ourselves
        # exclude always wins
        for pat in self.exclude:
            if self._matches(pat, module):
                return False
        for pat in self.include:
            if self._matches(pat, module):
                return True
        return False

    # -- delta snapshot ----------------------------------------------------

    def _snapshot(self, frame):
        """Return {name: value} for the changed locals since last snapshot.

        Values are frozen before they are stored, so the snapshot reflects the
        state at this control point rather than aliasing objects that the
        program keeps mutating afterwards.
        """
        key = id(frame)
        prev = self._snapshots.get(key)
        current = {name: freeze_value(value) for name, value in frame.f_locals.items()}
        if prev is None:
            changed = dict(current)
        else:
            changed = {name: value for name, value in current.items()
                       if prev.get(name) != value}
        if self.max_locals is not None and len(changed) > self.max_locals:
            changed = dict(list(changed.items())[:self.max_locals])
        self._snapshots[key] = current
        return changed

    # -- trace hook --------------------------------------------------------

    def _hook(self, frame, event, arg):
        self._event_count += 1
        code = frame.f_code
        if event == "call":
            if self._wanted(code):
                initial = {name: freeze_value(value) for name, value in frame.f_locals.items()}
                self.writer.add_call(
                    code.co_qualname if hasattr(code, "co_qualname") else code.co_name,
                    frame.f_lineno,
                    initial,                # initial state: the arguments
                )
                self._snapshots[id(frame)] = dict(initial)
                return self._hook
            return None
        if event == "return":
            if id(frame) in self._snapshots:
                self.writer.add_return(
                    code.co_qualname if hasattr(code, "co_qualname") else code.co_name
                )
                del self._snapshots[id(frame)]
            return None
        if event == "exception":
            if id(frame) in self._snapshots:
                self.writer.add_exception(
                    code.co_qualname if hasattr(code, "co_qualname") else code.co_name,
                    frame.f_lineno,
                    self._snapshot(frame),
                )
            return self._hook
        if event == "line":
            if id(frame) in self._snapshots:
                self.writer.add_line(
                    code.co_qualname if hasattr(code, "co_qualname") else code.co_name,
                    frame.f_lineno,
                    self._snapshot(frame),
                )
            return self._hook
        return self._hook

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        self._old_hook = sys.gettrace()
        sys.settrace(self._hook)

    def stop(self):
        sys.settrace(self._old_hook if self._old_hook is not None else None)

    @property
    def stats(self):
        return {"events_seen": self._event_count}


def record(script, outfile, include=("*",), exclude=("tracewire",), argv=None,
           max_locals=None, raise_on_error=False):
    """Run *script* under instrumentation and write a trace to *outfile*.

    Returns the :class:`TraceWriter` used.  The traced program runs in a
    subprocess-less but isolated manner: the current process is reused, so
    call this once per process (see ``tracewire record``).

    The trace is always written - a crash is exactly the case a post-mortem
    debugger exists for.  ``raise_on_error`` makes the failure visible to the
    caller (and therefore to the shell's exit status) instead of only being
    printed.
    """
    writer = core.TraceWriter(
        srcfile=os.path.abspath(script),
        modules=list(include),
    )
    tracer = Tracer(writer, include=include, exclude=exclude, max_locals=max_locals)

    # ---- everything that is NOT the user's program happens before tracing ---
    # Tracing must start as late as possible: any library call made between
    # start() and the user's first line is recorded as if it were user code.
    # Do the path juggling, file read and compile up front, so the only thing
    # executed while the hook is live is the program itself.  (This cost 193
    # junk events from ntpath/posixpath on Python 3.10.)
    script_abs = os.path.abspath(script)
    with open(script, "r", encoding="utf-8") as fh:
        source = fh.read()
    code = compile(source, script, "exec")
    globals_ = {"__name__": "__main__", "__file__": script_abs}
    script_dir = os.path.dirname(script_abs)
    old_argv = sys.argv
    old_path = sys.path[:]
    error = None
    exit_code = 0
    sys.argv = [script] + list(argv or [])
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    tracer.start()
    try:
        try:
            exec(code, globals_)
        except SystemExit as exc:
            if exc.code not in (0, None):
                error = exc
                exit_code = exc.code if isinstance(exc.code, int) else 1
        except BaseException as exc:   # noqa: BLE001 - the trace matters most
            error = exc
            exit_code = 1
    finally:
        # Stop tracing *before* anything else runs: while the hook is active,
        # printing a traceback records thousands of traceback/linecache/
        # tokenizer internals into the trace.  That buried the user's frame.
        tracer.stop()
        sys.argv = old_argv
        sys.path[:] = old_path
    writer.write(outfile)
    writer.error = error
    writer.exit_code = exit_code
    if error is not None:
        if raise_on_error:
            raise ProgramFailed(error, exit_code) from error
        if isinstance(error, SystemExit):
            print(f"tracewire: traced program exited with status {exit_code}", file=sys.stderr)
        else:
            _traceback.print_exception(type(error), error, error.__traceback__)
    return writer
