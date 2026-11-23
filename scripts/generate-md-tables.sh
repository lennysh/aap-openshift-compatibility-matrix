#!/bin/bash
# Regenerate AAP_<ver>.md at the repo root for every data/AAP_<ver>.csv, and the list of
# version links in README.md (between the BEGIN/END AAP VERSIONS markers).
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

# Sort numerically by major then minor (so 2.10 comes after 2.9)
mapfile -t csvs < <(
    for csv in "${csvs[@]}"; do
        digits="$(basename "${csv}" .csv)"; digits="${digits#AAP_}"
        [[ "${digits}" =~ ^[0-9]{2,}$ ]] && printf '%s %s %s\n' "${digits:0:1}" "${digits:1}" "${csv}"
    done | sort -k1,1n -k2,2n | cut -d' ' -f3-
)

links=""
for csv in "${csvs[@]}"; do
    base="$(basename "${csv}" .csv)"   # AAP_27
    digits="${base#AAP_}"              # 27
    version="${digits:0:1}.${digits:1}"
    echo "Generating ${base}.md (AAP ${version})"
    bash ./scripts/csv2md.sh \
        -t "Red Hat Ansible Automation Platform ${version} - OpenShift Operator Component versions" \
        "${csv}" > "${base}.md"
    links+="* [**Red Hat Ansible Automation Platform ${version}**](./${base}.md)"$'\n'
done

# Refresh the version list in README.md
begin='<!-- BEGIN AAP VERSIONS'
end='<!-- END AAP VERSIONS -->'
if grep -q "^${begin}" README.md && grep -q "^${end}" README.md; then
    tmp="$(mktemp)"
    LINKS="${links}" awk -v begin="${begin}" -v end="${end}" '
        index($0, begin) == 1 { print; printf "%s", ENVIRON["LINKS"]; skip = 1; next }
        index($0, end) == 1   { skip = 0 }
        !skip
    ' README.md > "${tmp}"
    cat "${tmp}" > README.md
    rm -f "${tmp}"
    echo "Updated version list in README.md"
else
    echo "README.md version markers not found; skipping link list" >&2
fi
