#!/usr/bin/env bash
# Local Foundry campaigns for the three V2 fuzz-report gaps. No RPC or signer is used.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
task_output="${1:-/tmp/v2-fuzz-supplement}"
mkdir -p "$task_output"
task_output="$(cd "$task_output" && pwd)"
task_fuzz_runs="${V2_FUZZ_RUNS:-1024}"
task_capacity_runs="${V2_CAPACITY_FUZZ_RUNS:-256}"
export FOUNDRY_INVARIANT_RUNS="${V2_INVARIANT_RUNS:-256}"
export FOUNDRY_INVARIANT_DEPTH="${V2_INVARIANT_DEPTH:-128}"
export FOUNDRY_INVARIANT_FAIL_ON_REVERT=true
task_threads="${V2_FUZZ_THREADS:-4}"
read -r -a task_seeds <<< "${V2_FUZZ_SEEDS:-0x2026100301 0x2026100302 0x2026100303}"

{
  forge --version
  git rev-parse HEAD
  git status --short
  printf 'fuzz_runs=%s capacity_runs=%s invariant_runs=%s invariant_depth=%s threads=%s\n' \
    "$task_fuzz_runs" "$task_capacity_runs" "$FOUNDRY_INVARIANT_RUNS" "$FOUNDRY_INVARIANT_DEPTH" "$task_threads"
  printf 'seeds=%s\n' "${task_seeds[*]}"
  shasum -a 256 test/V2AssetPercentInvariant.t.sol test/V2AssetPercentLimitsFuzz.t.sol \
    test/V2EngineCostInvariant.t.sol test/V2DustProgressFuzz.t.sol foundry.toml
} > "$task_output/metadata.txt"
git ls-files src/v2 | sort | while IFS= read -r task_source; do
  shasum -a 256 "$task_source"
done > "$task_output/v2-source-sha256.txt"

task_run() {
  local task_label="$1"
  local task_runs="$2"
  shift 2
  printf 'Running %s, seed %s\n' "$task_label" "$task_seed"
  local task_started
  task_started="$(date +%s)"
  printf '%q ' forge test --offline --threads "$task_threads" --fuzz-seed "$task_seed" --fuzz-runs "$task_runs" \
    "$@" -vv > "$task_output/${task_seed}-${task_label}.command"
  printf '\n' >> "$task_output/${task_seed}-${task_label}.command"
  forge test --offline --threads "$task_threads" --fuzz-seed "$task_seed" --fuzz-runs "$task_runs" \
    "$@" -vv 2>&1 | tee "$task_output/${task_seed}-${task_label}.log"
  if ! grep -q '\[PASS\]' "$task_output/${task_seed}-${task_label}.log" \
      || grep -q '\[SKIP\]' "$task_output/${task_seed}-${task_label}.log"; then
    printf 'Missing passing tests or unexpected skipped tests in %s\n' "$task_label" >&2
    exit 1
  fi
  printf '%s %s %s\n' "$task_seed" "$task_label" "$(( $(date +%s) - task_started ))" >> "$task_output/durations.txt"
}

for task_seed in "${task_seeds[@]}"; do
  task_run percent-invariant "$task_fuzz_runs" --match-path test/V2AssetPercentInvariant.t.sol
  task_run percent-limits "$task_fuzz_runs" --match-path test/V2AssetPercentLimitsFuzz.t.sol
  task_run engine-cost "$task_fuzz_runs" --match-path test/V2EngineCostInvariant.t.sol
  task_run dust-progress "$task_fuzz_runs" --match-path test/V2DustProgressFuzz.t.sol --no-match-test '.*[Cc]apacity.*'
  task_run dust-capacity "$task_capacity_runs" --match-path test/V2DustProgressFuzz.t.sol --match-test '.*[Cc]apacity.*'
done
