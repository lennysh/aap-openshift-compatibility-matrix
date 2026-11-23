#!/usr/bin/env python3
"""
Build OCP_INDEX_COVERAGE.md from scripts/csv-overlaps.json (written by the Ansible playbook).

Writes one markdown table per AAP release (major.minor from the bundle id, e.g.
`aap-operator.v2.4.0-…` → AAP 2.4). Columns: operator CSV | build time (UTC,
from the `-0.<unix>` suffix when it is a plausible epoch) | OCP index tags.
Rows are sorted by full CSV version within each section.

If any OCP index scrape failed, an existing report is left untouched so a partial
scrape never publishes a table with OCP versions missing. Index tags that are not
published yet (status "tag_not_found", e.g. a future OCP release) do not count as failures.

Usage: python3 scripts/csv-overlaps-to-markdown.py [input_json] [-o OUTPUT]
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path


_CSV_SORT_RE = re.compile(
    r"^aap-operator\.v(?P<maj>\d+)\.(?P<min>\d+)\.(?P<pat>\d+)-0\.(?P<build>\d+)$"
)

# OLM bundle suffix `-0.<digits>` is typically a Unix build timestamp (seconds).
_BUILD_TS_MIN = 1_000_000_000  # ~2001-09-09
_BUILD_TS_MAX = 2_500_000_000  # ~2049-07-10


def csv_sort_key(name: str) -> tuple:
    m = _CSV_SORT_RE.match(name.strip())
    if m:
        return (
            int(m.group("maj")),
            int(m.group("min")),
            int(m.group("pat")),
            int(m.group("build")),
        )
    return (999, 999, 999, name)


def ocp_sort_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


def md_escape_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def build_suffix_as_utc_datetime(csv_name: str) -> str:
    """Decode trailing `-0.<digits>` as Unix seconds → UTC, if in a plausible range."""
    m = _CSV_SORT_RE.match(csv_name.strip())
    if not m:
        m = re.search(r"-0\.(?P<build>\d+)$", csv_name.strip())
        if not m:
            return ""
    try:
        ts = int(m.group("build"))
    except (TypeError, ValueError):
        return ""
    if not (_BUILD_TS_MIN <= ts <= _BUILD_TS_MAX):
        return ""
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def aap_mm_tuple(name: str) -> tuple[int, int] | None:
    """First two version segments after `v` (e.g. v2.2.2 → (2,2), v2.4.0 → (2,4))."""
    m = _CSV_SORT_RE.match(name.strip())
    if m:
        return int(m.group("maj")), int(m.group("min"))
    return None


def group_by_aap_mm(
    presence: dict[str, set[str]],
) -> list[tuple[tuple[int, int], list[str]]]:
    buckets: dict[tuple[int, int], list[str]] = {}
    other: list[str] = []
    for csv_name in presence:
        mm = aap_mm_tuple(csv_name)
        if mm is None:
            other.append(csv_name)
            continue
        buckets.setdefault(mm, []).append(csv_name)
    out: list[tuple[tuple[int, int], list[str]]] = []
    for mm in sorted(buckets.keys(), key=lambda t: (t[0], t[1])):
        names = sorted(buckets[mm], key=csv_sort_key)
        out.append((mm, names))
    if other:
        out.append(((-1, -1), sorted(other, key=csv_sort_key)))
    return out


def invert_by_ocp(data: dict) -> dict[str, set[str]]:
    presence: dict[str, set[str]] = {}
    by_ocp = data.get("csv_versions_by_ocp") or {}
    for ocp, names in by_ocp.items():
        if not isinstance(names, list):
            continue
        for n in names:
            if isinstance(n, str) and n.strip():
                presence.setdefault(n.strip(), set()).add(ocp)
    return presence


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate markdown from csv-overlaps.json (table: CSV -> OCP tags)."
    )
    ap.add_argument(
        "input_json",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parent / "csv-overlaps.json",
        help="Path to csv-overlaps.json",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output .md path (default: OCP_INDEX_COVERAGE.md at the repo root)",
    )
    args = ap.parse_args()

    src = args.input_json
    if not src.is_file():
        print(f"Error: not found: {src}", file=sys.stderr)
        return 1

    data = json.loads(src.read_text(encoding="utf-8"))
    presence = invert_by_ocp(data)
    if not presence:
        print("Error: no csv_versions_by_ocp data to render", file=sys.stderr)
        return 1

    out = args.output
    if out is None:
        out = Path(__file__).resolve().parent.parent / "OCP_INDEX_COVERAGE.md"

    summary = data.get("summary") or {}

    # Per-OCP scrape status. "ok*" = index pulled; "tag_not_found" = index not published yet
    # (expected for future OCP releases). Anything else is a real failure.
    by_ocp = (data.get("scrape_report") or {}).get("by_ocp") or {}
    statuses = {str(k): str((v or {}).get("status", "unknown")) for k, v in by_ocp.items()}
    not_published = sorted(
        (v for v, st in statuses.items() if st == "tag_not_found"), key=ocp_sort_key
    )
    failed = sorted(
        (v for v, st in statuses.items() if not st.startswith("ok") and st != "tag_not_found"),
        key=ocp_sort_key,
    )
    if failed and out.exists():
        print(
            f"::warning::Index scrape failed for OCP {', '.join(failed)}; "
            f"leaving {out.name} unchanged",
            file=sys.stderr,
        )
        return 0
    checked = summary.get("ocp_versions_checked") or []
    if isinstance(checked, list):
        checked_line = ", ".join(str(x) for x in sorted(checked, key=ocp_sort_key))
    else:
        checked_line = str(checked)

    lines: list[str] = []
    lines.append("# AAP Operator Builds by OpenShift Operator Index")
    lines.append("")
    lines.append(
        "Every Ansible Automation Platform operator build (CSV) currently published in the "
        "Red Hat operator index, and the OpenShift versions whose index lists it. "
        "Generated automatically from the operator index scrape; do not edit by hand."
    )
    lines.append("")
    if checked_line:
        lines.append(f"**OCP index tags scanned:** {checked_line}")
        lines.append("")
    if not_published:
        lines.append(f"**OCP index tags not published yet:** {', '.join(not_published)}")
        lines.append("")
    if "total_unique_csv_versions" in summary:
        lines.append(f"**Unique operator CSVs:** {summary['total_unique_csv_versions']}")
        lines.append("")

    lines.append("## CSV → OpenShift index versions (by AAP release)")
    lines.append("")

    for mm, csv_names in group_by_aap_mm(presence):
        if mm == (-1, -1):
            lines.append("### Other (unrecognized operator CSV name)")
        else:
            lines.append(f"### AAP {mm[0]}.{mm[1]}")
        lines.append("")
        lines.append(
            "| Operator CSV | Build time (UTC, from `-0.` suffix) | OpenShift index versions (OCP) |"
        )
        lines.append("| --- | --- | --- |")
        for csv_name in csv_names:
            ocps = sorted(presence[csv_name], key=ocp_sort_key)
            ocp_cell = ", ".join(ocps)
            when = build_suffix_as_utc_datetime(csv_name)
            when_cell = when if when else "—"
            lines.append(
                f"| `{md_escape_cell(csv_name)}` | {md_escape_cell(when_cell)} | {md_escape_cell(ocp_cell)} |"
            )
        lines.append("")

    out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
