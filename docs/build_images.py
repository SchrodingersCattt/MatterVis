"""
Thin wrapper that delegates to `paper/showcase/build_images.py`.

Run from the repository root:

    python docs/build_images.py

or directly:

    python paper/showcase/build_images.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Point to the canonical script in paper/showcase/
PAPER_SCRIPT = Path(__file__).resolve().parent.parent / "paper" / "showcase" / "build_images.py"

if __name__ == "__main__":
    sys.path.insert(0, str(PAPER_SCRIPT.parent.parent.parent))
    from paper.showcase.build_images import main

    main()
