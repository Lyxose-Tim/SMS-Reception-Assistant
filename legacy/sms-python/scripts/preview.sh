#!/bin/sh
# Internal QA launcher. Ignore framework-only flags appended by preview tooling.
set -eu
DEMO_PREVIEW_HOST=terminal.local
DEMO_INTERNAL_QA=1
export DEMO_PREVIEW_HOST DEMO_INTERNAL_QA
exec python3 app.py --port 4173
