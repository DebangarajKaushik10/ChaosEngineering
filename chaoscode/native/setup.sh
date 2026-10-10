#!/usr/bin/env bash
# One-time setup for running Hotel Reservation WITHOUT Docker (Linux or WSL2, x86-64).
# Needs: Go 1.21+, git, curl, memcached (sudo apt install memcached).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/bin"; mkdir -p "$BIN"

command -v memcached >/dev/null || { echo "Install memcached first: sudo apt install memcached"; exit 1; }

if [ ! -d "$HERE/DeathStarBench" ]; then
  git clone --depth 1 --filter=blob:none --sparse https://github.com/delimitrou/DeathStarBench.git "$HERE/DeathStarBench"
  git -C "$HERE/DeathStarBench" sparse-checkout set hotelReservation
fi
echo "Building the 10 Go services (offline, vendored dependencies)..."
(cd "$HERE/DeathStarBench/hotelReservation" && GOFLAGS=-mod=vendor go build -o "$BIN/" ./cmd/...)

echo "Downloading FerretDB (MongoDB-compatible) and Toxiproxy..."
curl -fsSL -o "$BIN/ferretdb-linux-amd64" \
  https://github.com/FerretDB/FerretDB/releases/download/v1.24.0/ferretdb-linux-amd64
curl -fsSL -o "$BIN/toxiproxy-server-linux-amd64" \
  https://github.com/Shopify/toxiproxy/releases/download/v2.9.0/toxiproxy-server-linux-amd64
chmod +x "$BIN"/*
echo "Done. Start with: python3 native/sandbox.py up"
