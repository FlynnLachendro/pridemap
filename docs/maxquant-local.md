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

### Why some `mqpar.xml` fields must be absent, others empty, and others sized

MaxQuant reads a number of `mqpar.xml` fields **without checking for null**, and indexes some of them
by the value of another field. A wrong one does not fail fast: it aborts 10–37 minutes into a run,
after the raw download and sometimes after the search itself has finished. `MaxQuantCmd --dryrun`
catches none of them, because building the job graph never dereferences those fields — a config can
plan all 49 jobs and still die at job 8.

"Absent", "present but empty" and "sized to match another field" are three different things here.
Six real failures were found one run at a time; each row is now pinned by a test.

| Field | Required form | If wrong |
|---|---|---|
| `multiplicity`, `maxLabeledAa` | present | `NullReferenceException` in `GroupParams.GetLmods()` — **job 7**, after feature detection succeeded |
| `labelMods` | exactly `multiplicity` entries (one empty `<string />` when label-free) | MaxQuant allocates its apl name table as `[fragmentationTypes × massAnalysers, multiplicity]` and indexes the label list by that same counter, so zero entries means `l[0]` on an empty array: *Index was outside the bounds of the array* — **job 8** |
| `msmsParamsArray` | all four analysers (`FTMS`, `ITMS`, `TOF`, `Unknown`) | looked up by the analyser name read from the raw file, so an Orbitrap run throws *The given key 'FTMS' was not present in the dictionary* — **job 8** |
| `fragmentationParamsArray` | all eight types (`CID`, `HCD`, `ETD`, `PQD`, `ETHCD`, `ETCID`, `UVPD`, `Unknown`) | same lookup-by-name pattern |
| `restrictMods` | present, with MaxQuant's defaults `Oxidation (M)`, `Acetyl (Protein N-term)` | absent → `NullReferenceException` in `Tables.ToModifications()` while preparing the combined folder, **job 19**. Empty parses, but silently changes which peptides count towards protein quantification — a wrong result rather than a crash |
| `referenceChannel` | **absent** | present-but-empty makes `MaxQuantParams.ReferenceChannels` throw `IndexOutOfRangeException` while `modificationSpecificPeptides.txt` is written — **job 48 of 49** |
| `enzymesFirstSearch`, `variableModificationsFirstSearch` | **absent** | the first search does run, and absent is the form a completed run was observed with; empty may mean "none at all" rather than "inherit the main search" |

Settings for workflows the kit never generates — isobaric labels, crosslinking, DIA paths — are
emitted present-and-empty, so any code path reaching them finds a list rather than null.

The values above are MaxQuant's own, not chosen by hand. They were read out of `MaxQuantLibS.dll` by
calling `WorkflowModelGlobalParams.GetGlobalParameters()` and then
`GetFragmentSpectrumSettings()` / the `Modifications used in protein quantification` GUI parameter.
`api/omics.py` is the single owner of the generated config; see `MSMS_PARAMS`, `FRAGMENTATION_TYPES`,
`RESTRICT_MODS` and `EMPTY_GROUP_LISTS` there.

### Checking a config before spending a download on it

Two checks, cheapest first. Run both after changing anything in `mqpar_xml()`.

```bash
# 1. Structural invariants — no Docker, ~1 second. Generates a config and asserts every row above.
uv run tests/test_mqpar_contract.py

# 2. MaxQuant's own libraries — needs the provisioned image, a few seconds.
#    Parses the config with MaxQuantParams.Read() and runs the accessors that actually abort a run.
tools/mqpar-validate/validate.sh path/to/mqpar.xml
```

The second one exits `0` when the config satisfies MaxQuant, `1` when a check fails (naming the field
and the job it would abort at), and `2` when it cannot validate here — no Docker, or the MaxQuant
image not provisioned yet. A `2` is a skip, not a pass.

Why both: the structural test is fast and runs anywhere, but only MaxQuant's reader truly agrees with
MaxQuant. The deep check is what distinguishes "the XML looks right" from "MaxQuant can use it", and
it caught encodings that parsed cleanly as XML yet threw inside a getter.

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
| `The given key 'FTMS' was not present in the dictionary` (job 8) | `msmsParamsArray` is missing from `mqpar.xml`. Re-download the kit, then `tools/mqpar-validate/validate.sh mqpar.xml` |
| `Index was outside the bounds of the array` during MS/MS preparation (job 8) | `labelMods` does not hold `multiplicity` entries. See the field table above |
| `NullReferenceException` in `GetLmods()` (job 7) | `multiplicity` / `maxLabeledAa` missing from the parameter group |
| `NullReferenceException` in `Tables.ToModifications()` (job 19) | `restrictMods` is absent, or a modification name does not exist in MaxQuant's tables |
| `IndexOutOfRangeException` writing `modificationSpecificPeptides` (job 48) | `referenceChannel` is present but empty; it must be absent |
| A run dies mid-stage and the re-run fails differently | MaxQuant's intermediates are not safely re-enterable mid-pipeline (`.peaksi` and the apl name table are rebuilt per stage). Delete `combined/` and `raw/<run-name>/` and let `run_all.sh` redo the search; the downloaded raw files are kept |
| Preflight: `MaxQuant not found` | Docker pulled fine but the image tag mismatched; re-run `preflight.sh` — it re-provisions |
| Disk full mid-run | `run_all.sh` resumes; free space and re-run it |
| PRIDE FTP timeout | Re-run `run_all.sh`; `download_raw.sh` checks files that are already complete before re-downloading |

## Results

When the run finishes, `run_all.sh` uploads `combined/txt/proteinGroups.txt` straight into the
**Analyse** tab in PRIDE Map. You can also find it locally at `combined/txt/proteinGroups.txt`
inside the kit directory.
