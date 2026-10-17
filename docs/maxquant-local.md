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
| **arm64 / Apple Silicon (M1/M2/M3)** | `preflight.sh` builds `pridemap/maxquant:2.0.3.0-native` once: copies the .NET assemblies out of the amd64 image without executing them, then wraps them in the multi-arch `mono:6.12` base. Mono's JIT runs in its native arm64 configuration, which is the path Mono is tested in. The one-time build takes a few minutes. |
| **Bruker raw data** | Bruker's vendor libraries (`libtimsdata.so`, `libbaf2sql_c.so`) are x86-64 only. `preflight.sh` forces the amd64 image and Docker's QEMU emulation on arm64 hosts. Emulation adds time but is the only option. |

### Why not emulation for arm64 non-Bruker?

Mono's JIT aborts in its own trampolines when running under QEMU (`SIGABRT: Assertion at …`). The
native rebuild avoids this; the amd64 image is only kept as a source of the .NET assemblies, not
executed on the arm64 host.

## Manual image management

```bash
# Pull the amd64 image
docker pull quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1

# Verify it runs (amd64 host or QEMU)
docker run --rm quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1 maxquant --version

# On arm64: the generated preflight.sh builds the native image automatically.
# To do it by hand, run preflight.sh once from any downloaded kit.
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `SIGABRT: Assertion at … should not be reached` | Running the amd64 image under QEMU on arm64; let `preflight.sh` build the native image instead |
| `Given path for proc folder does not exist` | Run from inside the kit directory (`cd PXD012345_kit && bash run_all.sh`); `run_all.sh` substitutes absolute paths at startup |
| Preflight: `MaxQuant not found` | Docker pulled fine but the image tag mismatched; re-run `preflight.sh` — it re-provisions |
| Disk full mid-run | `run_all.sh` resumes; free space and re-run it |
| PRIDE FTP timeout | Re-run `run_all.sh`; `download_raw.sh` checks files that are already complete before re-downloading |

## Results

When the run finishes, `run_all.sh` uploads `combined/txt/proteinGroups.txt` straight into the
**Analyse** tab in PRIDE Map. You can also find it locally at `combined/txt/proteinGroups.txt`
inside the kit directory.
