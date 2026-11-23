#!/bin/bash
# Regenerate AAP_<ver>.md at the repo root for every data/AAP_<ver>.csv.
# Version is derived from the filename: AAP_27.csv -> "2.7", AAP_210.csv -> "2.10".
#
# Usage: ./scripts/generate-md-tables.sh   (run from anywhere)

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

shopt -s nullglob
csvs=(data/AAP_*.csv)
if (( ${#csvs[@]} == 0 )); then
    echo "No data/AAP_*.csv files found." >&2
    exit 1
fi

for csv in "${csvs[@]}"; do
    base="$(basename "${csv}" .csv)"   # AAP_27
    digits="${base#AAP_}"              # 27
    if [[ ! "${digits}" =~ ^[0-9]{2,}$ ]]; then
        echo "Skipping ${csv}: expected AAP_<major><minor>.csv" >&2
        continue
    fi
    version="${digits:0:1}.${digits:1}"
    echo "Generating ${base}.md (AAP ${version})"
    bash ./scripts/csv2md.sh \
        -t "Red Hat Ansible Automation Platform ${version} - OpenShift Operator Component versions" \
        "${csv}" > "${base}.md"
done
