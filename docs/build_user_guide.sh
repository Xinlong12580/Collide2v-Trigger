#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

pandoc USER_GUIDE.md -o USER_GUIDE.pdf \
  --pdf-engine=pdflatex --toc --toc-depth=1 --number-sections \
  --highlight-style=tango \
  -V papersize=a4 -V fontsize=11pt -V geometry:margin=22mm \
  -V colorlinks=true

echo "Saved docs/USER_GUIDE.pdf"
