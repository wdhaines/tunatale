#!/usr/bin/env python3
"""Print how often each write route was used, per day (tunatale-5i1p.1).

Usage: cd backend && uv run python scripts/report_action_counts.py [path/to/actions.log]
"""

import sys

from app.action_log import main

raise SystemExit(main(sys.argv[1:]))
