"""Shared display helpers for tree-view output.

Used by doctor.py and session_doctor.py for consistent formatting.
"""

from .runner import Colors

_C = Colors

# Status symbols
PASS = f"{_C.GREEN}\u2713{_C.NC}"
WARN = f"{_C.YELLOW}\u26a0{_C.NC}"
FAIL = f"{_C.RED}\u2717{_C.NC}"

# Tree-drawing characters (UTF-8 box drawing)
TREE_BRANCH = "\u251c\u2500\u2500 "
TREE_LAST = "\u2514\u2500\u2500 "
TREE_VERTICAL = "\u2502   "
TREE_SPACE = "    "
