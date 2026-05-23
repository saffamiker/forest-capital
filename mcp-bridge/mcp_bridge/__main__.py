"""Allows `python -m mcp_bridge ...` as a synonym for `bridge ...`.
Useful when the operator has the package installed but doesn't
have the console-script shim on PATH yet."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
