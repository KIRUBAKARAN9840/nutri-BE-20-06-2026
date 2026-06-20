#!/usr/bin/env bash
# safe-plan.sh — runs `terraform plan` and refuses to suggest apply
# unless the plan shows zero changes.
#
# Usage:
#   ./scripts/safe-plan.sh environments/prod

set -euo pipefail

DIR="${1:-environments/prod}"
PLAN_FILE="/tmp/tf-plan-$(date +%s).plan"

cd "$DIR"

echo "▶ terraform plan in $DIR ..."
terraform plan -out="$PLAN_FILE"

# Parse the plan for "to add", "to change", "to destroy" counts
SUMMARY=$(terraform show "$PLAN_FILE" 2>/dev/null | grep -E "Plan:" | tail -1 || true)
echo
echo "$SUMMARY"

if echo "$SUMMARY" | grep -qE "0 to add, 0 to change, 0 to destroy"; then
  echo "✅ Plan is clean. Safe to apply."
else
  echo
  echo "⚠️  Plan shows changes. Review carefully before running apply."
  echo "    - Adds:    expected for resources you've just defined"
  echo "    - Changes: review each one. Drift? Or HCL mismatch?"
  echo "    - Destroys: NEVER apply without rewriting HCL to match reality."
  echo
  exit 1
fi
