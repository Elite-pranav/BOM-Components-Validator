"""
Package entry point — allows running via `python -m backend`.

Starts the web server by default, or CLI mode with --cli flag.
"""

import sys
from backend.main import run_cli

if "--cli" in sys.argv:
    cli_args = [a for a in sys.argv[1:] if a != "--cli"]
    run_cli(cli_args)
else:
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
