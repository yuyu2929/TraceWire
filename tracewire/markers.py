"""tracewire.markers -- marker sets, Python predicates, post-mortem queries.

The trace is replayed into a stream of *markers* -- one per control point.
Each marker exposes:

    source      -- source file name
    line, col   -- location of the control point (col is best-effort)
    function    -- enclosing function name
    variables   -- full snapshot of local variables at this point
    changed     -- variables that differ from the previous point
    types       -- name -> type name mapping for the snapshot

Markers can be grouped into named *sets*.  A set is either assembled
manually or selected by a *predicate* -- a Python expression / function
evaluated against each marker.  Each set has two feature flags, exactly
as specified in the project idea:

    visible   -- when False the markers in the set are hidden and the wire
                 connecting them is simplified (rendered as a straight
                 segment), producing less visual garbage.
    important -- the viewer offers a pair of back/forward buttons for the
                 set, jumping to the previous/next marker of that set.

Sets can be saved to disk (JSON) and reloaded, so building a set once is
memoized across sessions.
"""

from __future__ import annotations

import json
import os


class Marker:
    __slots__ = ("index", "source", "line", "col", "function", "variables",
                 "changed", "types", "evtype")

    def __init__(self, index, source, line, col, function, variables, changed, evtype):
        self.index = index
        self.source = source
        self.line = line
        self.col = col
        self.function = function
        self.variables = dict(variables)
        self.changed = dict(changed)
        self.types = {k: type(v).__name__ for k, v in variables.items()}
        self.evtype = evtype

    # predicate convenience accessors --------------------------------------
    def get(self, name, default=None):
        return self.variables.get(name, default)

    def has(self, name):
        return name in self.variables

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"Marker(index={self.index}, {self.function} line {self.line}, "
                f"{len(self.variables)} vars)")


class TraceModel:
    """Replays a trace file into an ordered list of Markers.

    Full variable snapshots are reconstructed by folding the per-event
    deltas into per-frame state; ``changed`` keeps the raw delta so
    predicates can ask "what moved since the last control point".
    """

    def __init__(self, reader):
        self.reader = reader
        self.source = reader.srcfile
        self.markers = []
        self._build()

    def _build(self):
        # per-function stack of frame states, so recursion never conflates
        # the outer frame with an inner invocation of the same function
        frames = {}
        idx = 0
        for ev in self.reader.events:
            if ev.evtype == 2:  # EV_CALL: push entry state (arguments)
                frames.setdefault(ev.func_id, []).append(dict(ev.vars))
                continue
            if ev.evtype == 3:  # EV_RETURN: pop
                stack = frames.get(ev.func_id)
                if stack:
                    stack.pop()
                continue
            stack = frames.get(ev.func_id)
            state = stack[-1] if stack else {}
            if ev.vars:
                state = dict(state)
                state.update(ev.vars)
                if stack:
                    stack[-1] = state
            self.markers.append(Marker(
                index=idx,
                source=self.reader.srcfile,
                line=ev.line,
                col=0,                     # col requires source mapping; best-effort
                function=self.reader.func_name(ev.func_id),
                variables=state,
                changed=ev.vars,
                evtype=ev.evtype,
            ))
            idx += 1

    def __len__(self):
        return len(self.markers)


# --------------------------------------------------------------------------
# predicates

def make_predicate(source_text: str):
    """Compile a predicate from source.

    The predicate may be either an expression (evaluated with ``marker``
    in scope) or a function definition (``def any_name(marker): ...``).  A
    predicate is *any* Python callable/expression returning a truthy value.
    """
    src = source_text.strip()
    if not src:
        raise ValueError("empty predicate")
    namespace = {"Marker": Marker}
    if src.startswith("def ") or src.startswith("async def "):
        before = set(namespace)
        exec(compile(src, "<predicate>", "exec"), namespace)
        # The function does not have to be called `pred`; take whatever single
        # callable the snippet defined.  (It used to hard-require `pred` and
        # failed with a bare KeyError.)
        defined = [v for k, v in namespace.items()
                   if k not in before and callable(v) and not k.startswith("__")]
        if not defined:
            raise ValueError("predicate source does not define a callable")
        if len(defined) > 1:
            chosen = namespace.get("pred")
            if not callable(chosen):
                raise ValueError(
                    "predicate source defines several callables; name the one to use `pred`"
                )
            return chosen
        return defined[0]
    # expression form: bind `marker` as the lambda parameter
    code = compile(f"lambda marker: ({src})", "<predicate>", "eval")
    return eval(code, namespace)


class MarkerSet:
    def __init__(self, name, predicate_src=None, visible=True, important=False):
        self.name = name
        self.predicate_src = predicate_src
        self.visible = bool(visible)
        self.important = bool(important)
        self._predicate = make_predicate(predicate_src) if predicate_src else None
        # Number of markers on which the predicate raised.  A typo such as
        # `marker.total` used to select nothing and look like a valid but
        # empty set; the CLI reports this count instead.
        self.skipped = 0

    def contains(self, marker: Marker) -> bool:
        if self._predicate is None:
            return True
        try:
            return bool(self._predicate(marker))
        except Exception:
            self.skipped += 1
            return False

    def select(self, markers):
        return [m for m in markers if self.contains(m)]

    def to_dict(self):
        return {
            "name": self.name,
            "predicate": self.predicate_src or "",
            "visible": self.visible,
            "important": self.important,
        }

    @staticmethod
    def from_dict(data):
        name = data.get("name")
        if not name:
            raise ValueError("marker set is missing a name")
        return MarkerSet(
            name=name,
            predicate_src=data.get("predicate") or None,
            visible=data.get("visible", True),
            important=data.get("important", False),
        )


# --------------------------------------------------------------------------
# sets persistence (memoization to an on-disk file)

class SetStore:
    def __init__(self, path):
        self.path = path
        self.load_error = None
        self.sets = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            # A half-written or hand-edited memo file must not make every
            # subsequent dump/sets/html command fail.
            self.load_error = str(exc)
            return {}
        if not isinstance(data, dict):
            self.load_error = "expected a JSON object of set definitions"
            return {}
        out = {}
        skipped = []
        for name, entry in data.items():
            try:
                out[name] = MarkerSet.from_dict(entry)
            except Exception as exc:
                skipped.append(f"{name}: {exc}")
        if skipped:
            self.load_error = "ignored unusable set(s) - " + "; ".join(skipped)
        return out

    def save(self):
        # Write-then-replace: a crash mid-save must not truncate the memo.
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({n: s.to_dict() for n, s in self.sets.items()}, fh,
                      ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def add(self, marker_set: MarkerSet):
        self.sets[marker_set.name] = marker_set
        self.save()

    def get(self, name):
        return self.sets.get(name)

    def remove(self, name):
        self.sets.pop(name, None)
        self.save()
