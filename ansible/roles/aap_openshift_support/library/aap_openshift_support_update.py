#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: aap_openshift_support_update
short_description: Update OpenShift Support ranges in matrix CSVs from operator index scrape
description:
  - Reads csv-overlaps.json (csv_versions_by_ocp) and updates the OpenShift Support column.
  - Blank cells are filled from index coverage min/max OCP tags.
  - Existing ranges keep the lowest OCP (EOL floor) and extend the high end when index adds tags.
version_added: "1.0.0"
options:
  overlaps_json:
    description: Path to csv-overlaps.json from aap_operator_index_scrape.
    required: true
    type: path
  csv_paths:
    description: Matrix CSV files to update. Defaults to all AAP_*.csv under data_dir.
    required: false
    type: list
    elements: path
  data_dir:
    description: Directory containing AAP_*.csv when csv_paths is omitted.
    required: false
    type: path
author:
  - lennysh
'''

EXAMPLES = r'''
- name: Merge OpenShift Support from overlaps JSON
  aap_openshift_support_update:
    overlaps_json: "{{ aap_matrix_overlaps_json }}"
    data_dir: "{{ aap_matrix_data_dir }}"
'''

RETURN = r'''
changed:
  description: Whether any CSV file was modified.
  type: bool
messages:
  description: Per-row change log.
  type: list
rows_updated:
  description: Number of rows updated.
  type: int
'''

import csv
import glob
import json
import os
import re

from ansible.module_utils.basic import AnsibleModule

from ansible.module_utils.aap_operator_index import version_key

_RANGE_RE = re.compile(
    r"^\s*([0-9]+(?:\.[0-9]+)+)\s*-\s*([0-9]+(?:\.[0-9]+)+)\s*$"
)
_SINGLE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)+)\s*$")


def parse_openshift_cell(text):
    if not text or not text.strip():
        return None
    s = text.strip()
    match = _RANGE_RE.match(s)
    if match:
        return match.group(1), match.group(2)
    match = _SINGLE_RE.match(s)
    if match:
        return match.group(1), match.group(1)
    return None


def build_presence(data):
    by_ocp = data.get("csv_versions_by_ocp") or {}
    presence = {}
    for ocp, names in by_ocp.items():
        if not isinstance(names, list):
            continue
        for name in names:
            if isinstance(name, str) and name:
                presence.setdefault(name, set()).add(ocp)
    return presence


def ocp_span_for_csvs(presence, cluster_csv, namespace_csv):
    ocps = set()
    for name in (cluster_csv, namespace_csv):
        name = (name or "").strip()
        if name and name in presence:
            ocps |= presence[name]
    if not ocps:
        return None
    sorted_ocps = sorted(ocps, key=version_key)
    return sorted_ocps[0], sorted_ocps[-1]


def merge_range(existing_low, existing_high, json_low, json_high):
    lows = sorted([existing_low, json_low], key=version_key)
    highs = sorted([existing_high, json_high], key=version_key)
    return lows[0], highs[-1]


def process_csv(path, presence, module):
    messages = []
    rows_updated = 0

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        return False, messages, 0

    header = rows[0]
    try:
        idx_support = header.index("OpenShift Support")
        idx_cluster = header.index("Operator CSV (Cluster-scoped)")
        idx_namespace = header.index("Operator CSV (Namespace-scoped)")
    except ValueError as exc:
        module.warn("%s: missing column: %s" % (path, exc))
        return False, messages, 0

    changed_file = False
    out_rows = [header]

    for row_num, row in enumerate(rows[1:], start=2):
        if len(row) <= max(idx_support, idx_cluster, idx_namespace):
            out_rows.append(row)
            continue

        cell = row[idx_support]
        parsed = parse_openshift_cell(cell)
        cluster = row[idx_cluster].strip()
        namespace = row[idx_namespace].strip()

        span = ocp_span_for_csvs(presence, cluster, namespace)
        if span is None:
            out_rows.append(row)
            continue

        js_lo, js_hi = span
        if parsed is None:
            if not cell or not cell.strip():
                new_lo, new_hi = js_lo, js_hi
            else:
                module.warn(
                    "%s:%d: skip unparseable OpenShift Support: %r"
                    % (path, row_num, cell)
                )
                out_rows.append(row)
                continue
        else:
            ex_lo, ex_hi = parsed
            new_lo, new_hi = merge_range(ex_lo, ex_hi, js_lo, js_hi)

        new_cell = "%s-%s" % (new_lo, new_hi)
        if new_cell != cell.strip():
            messages.append("%s:%d: %r -> %r (index %s-%s)" % (
                path, row_num, cell, new_cell, js_lo, js_hi,
            ))
            row = list(row)
            row[idx_support] = new_cell
            rows_updated += 1
            changed_file = True

        out_rows.append(row)

    if changed_file:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(out_rows)

    return changed_file, messages, rows_updated


def resolve_csv_paths(data_dir, csv_paths):
    if csv_paths:
        return [os.path.abspath(os.path.expanduser(p)) for p in csv_paths]
    pattern = os.path.join(os.path.abspath(data_dir), "AAP_*.csv")
    return sorted(glob.glob(pattern))


def run(module):
    overlaps_path = os.path.abspath(
        os.path.expanduser(module.params["overlaps_json"])
    )
    if not os.path.isfile(overlaps_path):
        module.fail_json(msg="Overlaps file not found: %s" % overlaps_path)

    with open(overlaps_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    presence = build_presence(data)
    if not presence:
        module.exit_json(
            changed=False,
            messages=["No csv_versions_by_ocp data in overlaps JSON"],
            rows_updated=0,
        )

    data_dir = module.params.get("data_dir") or "data"
    if not os.path.isabs(data_dir):
        data_dir = os.path.join(os.getcwd(), data_dir)

    paths = resolve_csv_paths(data_dir, module.params.get("csv_paths"))
    if not paths:
        module.fail_json(msg="No matrix CSV files found under %s" % data_dir)

    all_messages = []
    total_rows = 0
    file_changed = False

    for path in paths:
        if not os.path.isfile(path):
            module.warn("Skip missing file: %s" % path)
            continue
        changed, messages, rows = process_csv(path, presence, module)
        file_changed = file_changed or changed
        total_rows += rows
        all_messages.extend(messages)

    all_messages.append("Done: %d row(s) updated." % total_rows)
    return {
        "changed": file_changed,
        "messages": all_messages,
        "rows_updated": total_rows,
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            overlaps_json=dict(type="path", required=True),
            csv_paths=dict(type="list", elements="path", required=False),
            data_dir=dict(type="path", required=False),
        ),
        supports_check_mode=False,
    )
    module.exit_json(**run(module))


if __name__ == "__main__":
    main()
