#!/usr/bin/env bash
# Validate a generated mqpar.xml against MaxQuant's own libraries, in seconds.
#
# WHY: MaxQuant reads several mqpar fields without null checks, and a wrong one aborts a run 10-37
# minutes in — after the raw download, sometimes after the search itself. `MaxQuantCmd --dryrun`
# catches none of them, because building the job graph never dereferences those fields. This runs the
# accessors that actually abort, so a bad config is caught before any data is fetched.
#
# usage:  tools/mqpar-validate/validate.sh <mqpar.xml>
#
# exit 0  config satisfies MaxQuant's accessors
# exit 1  a check failed (the output names the field and the job it would abort at)
# exit 2  cannot validate here (no Docker, or the MaxQuant image is not provisioned yet)
#
# Structural invariants are checked without Docker by tests/test_mqpar_contract.py; run that first.
#
# Interoperability check only: contains no MaxQuant code and runs against a separately licensed copy
# (MaxQuant freeware licence, see README › Licence). It reads parsed config values; it does not alter MaxQuant.
set -euo pipefail

MQPAR="${1:-}"
if [ -z "$MQPAR" ] || [ ! -f "$MQPAR" ]; then
  printf 'usage: %s <mqpar.xml>\n' "$0" >&2
  exit 2
fi

# The image preflight.sh provisions. Override to validate against a different build.
MQ_IMAGE="${MQ_IMAGE:-pridemap/maxquant:2.0.3.0-native}"
VALIDATOR_IMAGE="${VALIDATOR_IMAGE:-pridemap/mqpar-validate:latest}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  printf 'skipped: Docker is not running, so MaxQuant assemblies are unavailable.\n' >&2
  printf '         Structural checks still run: uv run tests/test_mqpar_contract.py\n' >&2
  exit 2
fi

if ! docker image inspect "$MQ_IMAGE" >/dev/null 2>&1; then
  printf 'skipped: %s is not present.\n' "$MQ_IMAGE" >&2
  printf '         Run a kit preflight once to provision it, or set MQ_IMAGE to an image that has\n' >&2
  printf '         MaxQuant assemblies in /opt/maxquant.\n' >&2
  exit 2
fi

# Build the validator once, on top of whichever MaxQuant image is in use. The base is a .NET SDK
# image, so it compiles in place and no toolchain is needed on the host. Rebuilt only when Validate.cs
# or the base image changes; Docker's cache decides that.
build_validator() {
  local ctx
  ctx="$(mktemp -d)"
  trap 'rm -rf "$ctx"' RETURN
  cp "$HERE/Validate.cs" "$ctx/Program.cs"
  cat > "$ctx/validate.csproj" <<'CSPROJ'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>netcoreapp3.1</TargetFramework>
    <AssemblyName>validate</AssemblyName>
    <RootNamespace>validate</RootNamespace>
    <Nullable>disable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="MaxQuantLibS"><HintPath>/opt/maxquant/MaxQuantLibS.dll</HintPath></Reference>
    <Reference Include="BaseLibS"><HintPath>/opt/maxquant/BaseLibS.dll</HintPath></Reference>
    <Reference Include="MsLib"><HintPath>/opt/maxquant/MsLib.dll</HintPath></Reference>
  </ItemGroup>
</Project>
CSPROJ
  cat > "$ctx/Dockerfile" <<DOCKERFILE
FROM $MQ_IMAGE
ENV DOTNET_CLI_HOME=/tmp DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
WORKDIR /src
COPY validate.csproj Program.cs ./
RUN dotnet build -c Release -o /opt/validate /src/validate.csproj \
 && ln -s /opt/maxquant/conf /opt/validate/conf
WORKDIR /data
ENTRYPOINT []
DOCKERFILE
  docker build --platform linux/amd64 -q -t "$VALIDATOR_IMAGE" "$ctx" >/dev/null
}

if ! docker image inspect "$VALIDATOR_IMAGE" >/dev/null 2>&1; then
  printf 'building the validator once (compiles against %s)\n' "$MQ_IMAGE"
  build_validator || { printf 'could not build the validator image\n' >&2; exit 2; }
fi

# Mount the config read-only under a fixed name, so the path inside the container never varies.
ABS_MQPAR="$(cd "$(dirname "$MQPAR")" && pwd)/$(basename "$MQPAR")"
exec docker run --rm \
  -v "$ABS_MQPAR":/data/mqpar.xml:ro \
  -w /opt/maxquant \
  "$VALIDATOR_IMAGE" \
  dotnet /opt/validate/validate.dll /data/mqpar.xml /opt/maxquant
