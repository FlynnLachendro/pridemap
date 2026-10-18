#!/usr/bin/env bash
# Build the MaxQuant image and assert its invariants locally, so an image fault surfaces in one
# local build instead of a remote Modal deploy. Every fault this guards against cost a deploy
# cycle to find, and Modal's build log is the only place they were visible.
#
# Python is deliberately NOT asserted here. Modal requires `python` and `pip` on $PATH and
# supplies them itself via add_python= in api/modal_runner.py, so they are absent from a plain
# local build. Their absence here is correct, not a fault — this script reports what it finds
# and judges only the invariants the Dockerfile itself owns.
#
# usage: docker/maxquant/verify-image.sh
#        IMAGE=my/tag docker/maxquant/verify-image.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-pridemap/maxquant-modal:verify}"

echo "==> building ${IMAGE} for linux/amd64 (emulated on arm64 hosts)"
docker build --platform=linux/amd64 -t "${IMAGE}" -f "${HERE}/Dockerfile" "${HERE}"

echo "==> asserting image invariants"
docker run --rm --platform=linux/amd64 --entrypoint sh "${IMAGE}" -c '
set -eu
fail() { echo "FATAL: $1" >&2; exit 1; }

echo "=== dotnet SDKs ==="
dotnet --list-sdks
# MaxQuant parses dotnet --info for a line starting "3.1"; a runtime-only image has none and
# aborts with "Please install .NET Core 3.1 SDK" before mqpar.xml is read.
dotnet --list-sdks | grep -q "^3\.1\." || fail "no 3.1 SDK line — DotNetCore31Installed() will reject this image"

echo "=== maxquant launcher ==="
command -v maxquant || fail "maxquant not on \$PATH"

echo "=== assemblies ==="
test -f /opt/maxquant/MaxQuantCmd.exe || fail "/opt/maxquant/MaxQuantCmd.exe missing — stage 1 COPY did not land"
test ! -e /opt/maxquant/global.json   || fail "global.json still present — it pins an SDK the image does not have"

echo "=== system python (informational: Modal injects its own via add_python) ==="
for b in python python3 pip pip3; do
  printf "%-8s %s\n" "$b" "$(command -v "$b" || echo MISSING)"
done

echo
echo "OK: image invariants hold"
'
