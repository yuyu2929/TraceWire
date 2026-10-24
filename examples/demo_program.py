"""Example program for TraceWire.

A deliberately simple program with changing state at every control point:
a recursive factorial plus an accumulation loop.  Run it under the tracer
and open the timeline to watch ``n``, ``f``, ``total`` and ``i`` change.

    python -m tracewire.cli record examples/demo_program.py -o demo.trace
    python -m tracewire.cli dump demo.trace
    python -m tracewire.cli html demo.trace -o demo.html
"""


def factorial(n):
    if n <= 1:
        return 1
    sub = factorial(n - 1)      # <- recursion is a good control point
    return n * sub


def accumulate(limit):
    total = 0
    for i in range(1, limit + 1):
        total += factorial(i)
        step = total - factorial(i)   # state that stays interesting
        print(f"i={i} total={total} (step base={step})")
    return total


def main():
    result = accumulate(4)
    print("final result:", result)
    return result


if __name__ == "__main__":
    main()
