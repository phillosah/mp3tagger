#!/usr/bin/env bash
set -e

echo "==> Installing Python dependencies"
pip install -r requirements.txt

echo "==> Downloading fpcalc (Chromaprint) for Linux"
FPCALC_VERSION="1.6.0"
FPCALC_URL="https://github.com/acoustid/chromaprint/releases/download/v${FPCALC_VERSION}/chromaprint-fpcalc-${FPCALC_VERSION}-linux-x86_64.tar.gz"
curl -fsSL "$FPCALC_URL" | tar -xz --strip-components=1 -C . \
  "chromaprint-fpcalc-${FPCALC_VERSION}-linux-x86_64/fpcalc"
chmod +x fpcalc
echo "==> fpcalc installed: $(./fpcalc -version)"
