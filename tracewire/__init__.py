"""TraceWire - a convenient tracer and post-mortem debugger for code.

TraceWire records an instrumented execution of a Python program as a
delta-compressed trace, and lets you inspect the execution afterwards as a
timeline ("wire") of markers.  Each marker carries the program state at a
control point; markers can be grouped into named sets using Python
predicates, and sets support "visible" (hide/simplify) and "important"
(quick forward/backward navigation) behaviours.

Modules
-------
- tracewire.core    : trace file format and delta compression codec.
- tracewire.tracer  : the instrumentation tracer (sys.settrace based).
- tracewire.markers : marker sets, Python predicates, post-mortem queries.
- tracewire.cli     : command line interface: record, dump, sets, html.
"""

__version__ = "0.2.0"

__all__ = ["core", "tracer", "markers", "cli"]
