#!/usr/bin/env bash
# Local unit/fuzz/invariant campaign. RPC forks are opt-in through a separate step.
set -euo pipefail
task_root="$(cd "$(dirname "$0")/../.." && pwd)"
task_forge="${FORGE_BIN:-$task_root/.local/bin/forge}"
task_python="${PYTHON_BIN:-python3}"
task_output="${FUZZ_OUTPUT:-$task_root/artifacts/fuzz-$(date -u +%Y%m%dT%H%M%SZ)}"
task_seed="${FUZZ_SEED:-0x20261004}"
task_runs="${FUZZ_RUNS:-4096}"
mkdir -p "$task_output"
export PATH="$(dirname "$task_forge"):$PATH"
"$task_forge" --version > "$task_output/toolchain.txt"
git -C "$task_root" rev-parse HEAD > "$task_output/commit.txt"
git -C "$task_root" diff --binary > "$task_output/working-tree.patch"
git -C "$task_root" submodule status --recursive > "$task_output/submodules.txt"
cd "$task_root/contractV2"
"$task_python" -m unittest discover -s tests -p 'test_*.py' > "$task_output/python-tests.log" 2>&1
"$task_python" -m unittest discover -s tools/tests -p 'test_*.py' > "$task_output/python-tools.log" 2>&1
"$task_forge" build --sizes > "$task_output/build-sizes.log" 2>&1
FOUNDRY_INVARIANT_RUNS="${INVARIANT_RUNS:-512}" FOUNDRY_INVARIANT_DEPTH="${INVARIANT_DEPTH:-500}" \
    "$task_forge" test --fuzz-runs "$task_runs" --fuzz-seed "$task_seed" -vv > "$task_output/v2.log" 2>&1
cd "$task_root/contractV1"
"$task_forge" test --fuzz-runs "$task_runs" --fuzz-seed "$task_seed" -vv > "$task_output/v1.log" 2>&1
echo "Campaign completed. Logs (including explicitly skipped opt-in cases): $task_output"
