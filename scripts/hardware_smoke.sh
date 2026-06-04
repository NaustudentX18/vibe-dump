#!/usr/bin/env bash
# Hardware smoke gate for the Vibe-Dump Pocket Pi build.
#
# Delegates the actual probing to the Python module so the rule lives in
# one place (importable by the runtime) and the shell script is just a
# thin launcher with strict mode + exit-code propagation.

set -euo pipefail

# Always run from the project root so the vibedump package resolves
# regardless of where the operator invokes the script from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

echo "Running hardware smoke check..."
python3 -m vibedump.integrations.hardware_probe
