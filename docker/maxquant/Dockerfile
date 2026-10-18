# Multi-stage build: copies MaxQuant assemblies from the biocontainers image onto the
# .NET SDK base.  This is the same transformation that preflight.sh performs locally
# (docker cp + docker build), expressed as a single reproducible Dockerfile so Modal
# can build it at deploy time without a separate registry push.
#
# Stage 1: source of MaxQuant assemblies (Conda / biocontainers build).
#   The assemblies live in /usr/local/bin/ in this image.
FROM quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1 AS source

# Stage 2: SDK base — identical to the local macOS build (see api/omics.py MAXQUANT_RUNTIME_BASE).
#   mcr.microsoft.com/dotnet/sdk:3.1-bullseye satisfies MaxQuant's DotNetCore31Installed() check:
#   `dotnet --info` outputs "3.1.426 [/usr/share/dotnet/sdk]", a line starting with "3.1".
#   The runtime-only image lacks SDK lines and the check fails with "Please install .NET Core 3.1 SDK".
#   Bullseye (Debian 11) is required: Buster (10) causes ThermoRawFileReader to fail.
FROM --platform=linux/amd64 mcr.microsoft.com/dotnet/sdk:3.1-bullseye

COPY --from=source /usr/local/bin/ /opt/maxquant/

RUN rm -f /opt/maxquant/global.json \
 && printf '#!/bin/sh\nexec dotnet /opt/maxquant/MaxQuantCmd.exe "$@"\n' \
      > /usr/local/bin/maxquant \
 && chmod +x /usr/local/bin/maxquant

WORKDIR /data
ENTRYPOINT []
