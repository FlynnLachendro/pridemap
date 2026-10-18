# Running MaxQuant locally in Docker

PRIDE Map's pipeline scripts run MaxQuant inside Docker so you need nothing installed beyond Docker
itself. The pipeline generates every script when you click **Download kit (zip)** or run
`GET /dataset/{accession}/pipeline.sh` from the API.

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker Desktop (macOS) or Docker Engine (Linux) | 20+ |
| ~10 GB free disk | ~1.4 GB for the image; the rest for raw files and results |
| x86-64 or arm64 host | arm64 covered — see [Architecture notes](#architecture-notes) |

## Quick start

```bash
# 1. Download the search kit for a dataset (the API must be running)
curl -fsSL http://localhost:8000/dataset/PXD012345/pipeline.sh | bash
```

Or manually:

```bash
# 1. Download and unzip the kit
curl -O "http://localhost:8000/dataset/PXD012345/kit.zip"
unzip PXD012345_kit.zip && cd PXD012345_kit

# 2. Run preflight — checks Docker, free disk, and provisions the image once
bash preflight.sh

# 3. Run the whole pipeline (download raw files → search → upload results)
bash run_all.sh
```

`run_all.sh` streams per-stage progress to stdout and writes a full record to `progress.jsonl`.
It is resume-safe: re-run it after an interruption and it picks up where it stopped.

## What the scripts do

| Script | Does |
|---|---|
| `preflight.sh` | Checks Docker, free disk, provisions the MaxQuant image (once), lists any blockers before the long download |
| `download_raw.sh` | Pulls the raw instrument files from PRIDE's FTP into `raw/` |
| `download_fasta.sh` | Downloads the UniProt reference proteome into `fasta/` |
| `convert_to_mzml.sh` | (DDA only, non-native formats) Converts raw files via ProteoWizard in Docker |
| `run_maxquant.sh` | Runs MaxQuant inside Docker on the downloaded data |
| `run_all.sh` | Calls the above in order, streams progress, exits on first failure |

## Architecture notes

MaxQuant ships as managed .NET (CIL) assemblies plus five native x86-64 Bruker libraries.

| Host | Behaviour |
|---|---|
| **x86-64 / amd64** (Linux or macOS Intel) | Uses the published Bioconda image `quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1` directly |
| **arm64 / Apple Silicon (M1/M2/M3), non-Bruker data** | `preflight.sh` builds `pridemap/maxquant:2.0.3.0-native` once using `mcr.microsoft.com/dotnet/sdk:3.1-bullseye` as the base. The container runs as `linux/amd64` under Docker's QEMU emulation; this is required for three reasons described below. The one-time build takes a few minutes. |
| **Bruker raw data (any host)** | Bruker's vendor libraries (`libtimsdata.so`, `libbaf2sql_c.so`) are x86-64 only — QEMU emulation is always used regardless of host architecture. |

### Why the dotnet SDK image is required on arm64 (three root causes)

Three independent bugs surface when running MaxQuant in Docker on Apple Silicon. The generated
`preflight.sh` + `run_all.sh` handle all three automatically.

**1. `DotNetCore31Installed()` check** — MaxQuant parses `dotnet --info` stdout looking for a line
that starts with `"3.1"` (an installed SDK). The runtime-only image (`dotnet/runtime:3.1`) has no
SDK lines and the check fails with *"Please install .NET Core 3.1 SDK"*. A wrapper script at
`/usr/bin/dotnet` is bypassed because dotnet resolves its own host binary path internally (not via
`PATH`). Fix: use `mcr.microsoft.com/dotnet/sdk:3.1-bullseye`, which natively outputs
`3.1.426 [/usr/share/dotnet/sdk]`.

**2. Worker subprocess ENOENT** — `QueueingSystem.WorkDispatcher.GetCommandFilename()` wraps the
executable path in literal `"` characters for Windows compatibility. On Linux `execve` receives
`""/opt/maxquant/MaxQuantTask.exe""` → ENOENT. Fix: `<useDotNetCore>True</useDotNetCore>` in
`mqpar.xml`; MaxQuant then launches workers as `dotnet MaxQuantTaskCore.dll <args>` with no embedded
quotes.

**3. ThermoRawFileReader symlink bug** — `RawFileReaderAdapter.FileFactory(path)` sets
`IsError=True` when given a symlink path, causing `SelectInstrument` to throw
`ArgumentOutOfRangeException`, which surfaces as *"NoSelectedDeviceException: RunHeaderEx"*. Fix:
`run_all.sh` resolves each symlink in `raw/` to its real path and rewrites the paths in
`mqpar.docker.xml` before running the container; the parent directory of the real file is
bind-mounted into the container.

### Why `3.1-bullseye` and not `3.1-buster` or `3.1-bullseye-slim`

- `3.1-bullseye-slim` does not exist in the Microsoft registry.
- `sdk:3.1` (Debian Buster / 10) causes ThermoRawFileReader to fail with a *NoSelectedDeviceException* — only Debian Bullseye (11) works.
- `sdk:3.1-bullseye` is the correct tag.

## Manual image management

```bash
# Pull the amd64 image (source of .NET assemblies)
docker pull quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1

# Build the native arm64-compatible image by hand (normally done by preflight.sh)
# Requires the .NET assemblies copied to bin/ first — see preflight.sh for the extract step
docker build --platform linux/amd64 -t pridemap/maxquant:2.0.3.0-native .

# Verify it runs
docker run --rm --platform linux/amd64 pridemap/maxquant:2.0.3.0-native maxquant --version
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Please install .NET Core 3.1 SDK` | Image is missing the SDK (runtime-only). Rebuild with `mcr.microsoft.com/dotnet/sdk:3.1-bullseye` as the base. |
| `NoSelectedDeviceException: RunHeaderEx` | ThermoRawFileReader received a symlink path. Re-run `preflight.sh` — it rebuilds the image and ensures `run_all.sh` resolves symlinks. Alternatively run from the kit directory so `run_all.sh` handles path rewriting. |
| `ENOENT` on MaxQuant worker launch | `<useDotNetCore>` is missing or false in `mqpar.xml`. The generated kit always includes it; re-download the kit if the error persists. |
| `Given path for proc folder does not exist` | Run from inside the kit directory (`cd PXD012345_kit && bash run_all.sh`); `run_all.sh` substitutes absolute paths at startup |
| Preflight: `MaxQuant not found` | Docker pulled fine but the image tag mismatched; re-run `preflight.sh` — it re-provisions |
| Disk full mid-run | `run_all.sh` resumes; free space and re-run it |
| PRIDE FTP timeout | Re-run `run_all.sh`; `download_raw.sh` checks files that are already complete before re-downloading |

## Results

When the run finishes, `run_all.sh` uploads `combined/txt/proteinGroups.txt` straight into the
**Analyse** tab in PRIDE Map. You can also find it locally at `combined/txt/proteinGroups.txt`
inside the kit directory.
