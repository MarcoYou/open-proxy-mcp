#!/bin/sh
export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"
S=$(gh run view 33456585810 --repo MarcoYou/open-proxy-mcp --json status -q .status 2>/dev/null)
[ "$S" = "completed" ]
