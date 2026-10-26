"""PyInstaller entry point for the student agent.

A wrapper (not smartclass/agent/__main__.py directly) so the package's relative
imports resolve when frozen.
"""
from smartclass.agent.__main__ import main

if __name__ == "__main__":
    main()
