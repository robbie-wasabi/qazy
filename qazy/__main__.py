"""Module entrypoint for ``python -m qazy``."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())

