#!/usr/bin/env bash
# Proves that the agent's ServiceAccount is read-only and cannot touch Secrets
# or ConfigMaps. Usage: rbac-check.sh [namespace]
set -euo pipefail
NS="${1:-ape-system}"
SA="system:serviceaccount:${NS}:ape-agent"
fail=0

rules=$(kubectl ${KUBECTL_CONTEXT:+--context $KUBECTL_CONTEXT} auth can-i --list --as="$SA")
echo "$rules"

# Any verb other than get/list/watch on a real resource is a failure. The
# selfsubject* review APIs (create) are granted to every authenticated user.
while read -r resource _ _ verbs; do
  case "$resource" in
    Resources|selfsubject*|"") continue ;;
  esac
  for verb in $(echo "$verbs" | tr -d '[]'); do
    case "$verb" in
      get|list|watch) ;;
      *) echo "FAIL: $SA can '$verb' $resource"; fail=1 ;;
    esac
  done
done <<<"$(echo "$rules" | awk 'NF>=4 && $2 !~ /^\[\/|^\[\*/')"

for resource in secrets configmaps; do
  for verb in get list watch; do
    if kubectl ${KUBECTL_CONTEXT:+--context $KUBECTL_CONTEXT} auth can-i "$verb" "$resource" --as="$SA" -A >/dev/null 2>&1; then
      echo "FAIL: $SA can $verb $resource"; fail=1
    fi
  done
done
for check in "create pods" "delete pods" "patch deployments.apps" "update nodes" "create pods/exec" \
             "create clusterrolebindings.rbac.authorization.k8s.io" "delete namespaces" "impersonate users"; do
  # shellcheck disable=SC2086
  if kubectl ${KUBECTL_CONTEXT:+--context $KUBECTL_CONTEXT} auth can-i $check --as="$SA" -A >/dev/null 2>&1; then
    echo "FAIL: $SA can $check"; fail=1
  fi
done
for check in "list pods" "list deployments.apps" "list clusterroles.rbac.authorization.k8s.io" "list nodes"; do
  # shellcheck disable=SC2086
  if ! kubectl ${KUBECTL_CONTEXT:+--context $KUBECTL_CONTEXT} auth can-i $check --as="$SA" -A >/dev/null 2>&1; then
    echo "FAIL: $SA cannot $check (needed for the audit)"; fail=1
  fi
done

[ "$fail" -eq 0 ] && echo "OK: $SA is read-only and has no access to secrets or configmaps"
exit "$fail"
