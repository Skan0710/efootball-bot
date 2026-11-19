"""Root launcher for the eFootball Discord bot."""
import sys
from pathlib import Path

# Add src/ to sys.path
_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from src.bot import main

if __name__ == "__main__":
    main()
