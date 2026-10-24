"""Examples of marker-set predicates for TraceWire.

A predicate is a Python expression or function evaluated against each
marker.  The marker exposes:

    marker.source    source file name
    marker.line      line number
    marker.function  enclosing function
    marker.variables full variable snapshot {name: value}
    marker.changed   variables whose value changed at this control point
    marker.types     {name: type-name}
    marker.evtype    1 = line, 4 = exception
    marker.has(name) / marker.get(name, default)

Run:

    python -m tracewire record examples/demo_program.py -o demo.trace
    python -m tracewire sets demo.trace --name recursion \
        --predicate "marker.function == 'factorial'"
    python -m tracewire sets demo.trace --name big_total --important \
        --predicate "marker.has('total') and marker.get('total') > 10"
    python -m tracewire dump demo.trace
    python -m tracewire html demo.trace -o demo.html

Note: variables are accessed with ``marker.get(name)``.  ``marker.total``
raises ``AttributeError``, which is treated as "no match" for that marker, so
the set would silently come out empty - the CLI now warns about that.
"""

# Expression form (evaluated with `marker` in scope):
EXPR_RECURSION = "marker.function == 'factorial'"
EXPR_BIG_TOTAL = "marker.has('total') and marker.get('total') > 10"

# Function form (any Python code, receives the marker):
FUNC_LOOP_END = """
def pred(marker):
    return marker.has('i') and marker.get('i') == 4
"""

# Important-set example: every marker inside the innermost recursion depth.
FUNC_DEEP = """
def pred(marker):
    return marker.function == 'factorial' and marker.get('n', 0) <= 2
"""
