#!/usr/bin/env bash
#
# What is the actual CI flake rate, per spec, with a denominator?
#
#   ./ci-flake-rate.sh          # last 60 runs
#   ./ci-flake-rate.sh 120      # last 120 runs
#
# ⚠️ WHY THIS EXISTS, AND THE TRAP IT CLOSES. The obvious query is
#
#     gh run list --json conclusion
#
# and it UNDERCOUNTS, silently. That field reports the LATEST ATTEMPT only, so
# any run where someone hit `gh run rerun --failed` and got green reports
# `success` and its failure vanishes from the count. On 2026-09-01 that produced
# a confident wrong finding twice over: first "1 flake in 60 runs", then the
# follow-on conclusion that re-running had DESTROYED the evidence and a manual
# recording habit was needed. Both wrong. GitHub retains every attempt
# (`/actions/runs/{id}/attempts/{n}/jobs`); only the summary field is lossy.
# Nothing was ever lost — the query was looking in the wrong place.
#
# So this script walks attempts, not runs. A rerun is now a data point rather
# than an erasure.
#
# ⚠️ ONE JOB failing is a flake. THE JOB SET THAT SHARES A SUITE failing
# together is a real break, not a flake — on 2026-08-31 all three backend jobs
# failed in lockstep five times while someone iterated on a genuine bug, which
# is 5 of the 6 failures in that window. The summary separates them, because
# averaging them together inflates the flake rate ~6x.
set -euo pipefail

REPO=wdhaines/tunatale
LIMIT="${1:-60}"

# Jobs that run the SAME backend suite. Two or more red together is a break.
SUITE_SIBLINGS='backend|backend-hostile-tz|backend-hostile-hour'

echo "Scanning the last $LIMIT runs of ci.yml on $REPO (all attempts)…" >&2

runs=$(gh run list --repo "$REPO" --workflow ci.yml --limit "$LIMIT" \
        --json databaseId,createdAt -q '.[] | "\(.databaseId)\t\(.createdAt[0:10])"')

total_attempts=0
declare -a rows=()

while IFS=$'\t' read -r id date; do
  [ -z "$id" ] && continue
  n=$(gh api "repos/$REPO/actions/runs/$id" --jq '.run_attempt' 2>/dev/null || echo 1)
  for a in $(seq 1 "$n"); do
    total_attempts=$((total_attempts + 1))
    failed=$(gh api "repos/$REPO/actions/runs/$id/attempts/$a/jobs" \
      --jq '.jobs[] | select(.conclusion=="failure") | .name' 2>/dev/null | sort | tr '\n' ',' || true)
    [ -z "$failed" ] && continue
    rows+=("$date|$id|$a|${failed%,}")
  done
done <<< "$runs"

echo
printf '%-11s %-12s %-4s %s\n' DATE RUN ATT "FAILED JOBS"
for r in "${rows[@]}"; do
  IFS='|' read -r d i a f <<< "$r"
  printf '%-11s %-12s %-4s %s\n' "$d" "$i" "$a" "$f"
done

# Classify: a failure touching 2+ suite siblings is a break, not a flake.
flakes=0; breaks=0
for r in "${rows[@]}"; do
  f="${r##*|}"
  sib=$(tr ',' '\n' <<< "$f" | grep -cE "^($SUITE_SIBLINGS)" || true)
  if [ "$sib" -ge 2 ]; then breaks=$((breaks + 1)); else flakes=$((flakes + 1)); fi
done

echo
echo "attempts scanned : $total_attempts"
echo "red attempts     : ${#rows[@]}  (breaks: $breaks, flakes: $flakes)"
if [ "$total_attempts" -gt 0 ]; then
  awk -v f="$flakes" -v n="$total_attempts" 'BEGIN{printf "flake rate       : %.1f%% (%d/%d)\n", 100*f/n, f, n}'
  # A rate needs its power stated or it invites the same mistake twice.
  awk -v f="$flakes" -v n="$total_attempts" 'BEGIN{
    if (f>0) { p=f/n; need=log(0.05)/log(1-p);
      printf "to see >=1 failure with 95%% confidence at this rate: %d attempts\n", (need==int(need)?need:int(need)+1) }
    else { printf "0 failures: 95%% upper bound on the rate is %.1f%%\n", 100*(1-exp(log(0.05)/n)) }
  }'
fi
