"""Allow `python -m vibe` (used by MiaouFlow to spawn workers on this fork)."""

from __future__ import annotations

from vibe.cli.entrypoint import main

if __name__ == "__main__":
    main()
