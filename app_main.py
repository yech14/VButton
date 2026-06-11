#!/usr/bin/env python3
"""Entry point for the py2app bundle.

The CLI script vbutton.py expects a subcommand in sys.argv. When launched
from Finder/Dock/launchd, argv has no subcommand, so we inject "run".
"""
import sys

if len(sys.argv) < 2:
    sys.argv.append("run")

import vbutton
vbutton.main()
