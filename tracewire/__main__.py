"""Allow ``python -m tracewire`` (the package used to require ``.cli``)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
