#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: aap_operator_index_overlaps_write
short_description: Assemble csv-overlaps.json from per-OCP scrape task results
description:
  - Builds csv-overlaps.json from registered results of aap_operator_index_scrape loop tasks.
version_added: "1.0.0"
options:
  scrape_results:
    description: List of registered results from an Ansible loop over aap_operator_index_scrape.
    required: true
    type: list
    elements: dict
  output_path:
    description: Path to write csv-overlaps.json.
    required: true
    type: path
  package_name:
    description: OLM package name recorded in scrape_report.
    required: false
    type: str
    default: ansible-automation-platform-operator
author:
  - lennysh
'''

EXAMPLES = r'''
- name: Write csv-overlaps.json
  aap_operator_index_overlaps_write:
    scrape_results: "{{ aap_openshift_support_scrape_loop.results }}"
    output_path: "{{ aap_matrix_overlaps_json }}"
    package_name: "{{ aap_matrix_operator_package }}"
'''

RETURN = r'''
changed:
  description: Whether the overlaps JSON file was written or updated.
  type: bool
msg:
  description: Summary message.
  type: str
'''

import json
import os

from ansible.module_utils.basic import AnsibleModule

from ansible.module_utils.aap_operator_index import version_key


def build_overlaps(csv_versions_by_ocp):
    presence = {}
    for ocp, names in csv_versions_by_ocp.items():
        for name in names:
            presence.setdefault(name, set()).add(ocp)

    overlaps = {}
    for csv_name, ocps in presence.items():
        if len(ocps) > 1:
            overlaps[csv_name] = sorted(ocps, key=version_key)
    return overlaps


def run(module):
    scrape_results = module.params["scrape_results"] or []
    output_path = os.path.abspath(os.path.expanduser(module.params["output_path"]))
    package_name = module.params["package_name"]

    csv_versions_by_ocp = {}
    scrape_report = {"package": package_name, "by_ocp": {}}
    ocp_versions_attempted = []

    for entry in scrape_results:
        if entry.get("skipped"):
            continue
        if entry.get("failed"):
            ocp = entry.get("item", "unknown")
            module.warn("Scrape failed for OCP %s" % ocp)
            csv_versions_by_ocp[str(ocp)] = []
            scrape_report["by_ocp"][str(ocp)] = {
                "status": "task_failed",
                "config_json_files": 0,
                "olm_channel_objects_merged": 0,
                "unique_operator_csv_count": 0,
            }
            ocp_versions_attempted.append(str(ocp))
            continue

        ocp = entry.get("ocp_version") or entry.get("item")
        if not ocp:
            continue
        ocp = str(ocp)
        ocp_versions_attempted.append(ocp)
        csv_versions_by_ocp[ocp] = entry.get("csv_versions") or []
        scrape_report["by_ocp"][ocp] = entry.get("scrape_report") or {
            "status": entry.get("status", "unknown"),
            "config_json_files": 0,
            "olm_channel_objects_merged": 0,
            "unique_operator_csv_count": entry.get("unique_operator_csv_count", 0),
        }

    if not ocp_versions_attempted:
        module.fail_json(msg="No scrape results to assemble")

    scrape_ok = [
        ocp
        for ocp, meta in scrape_report["by_ocp"].items()
        if meta.get("status") == "ok"
    ]
    ocp_with_csv = [ocp for ocp, names in csv_versions_by_ocp.items() if names]
    all_csv = sorted(
        {n for names in csv_versions_by_ocp.values() for n in names},
        key=lambda v: v,
    )
    overlaps = build_overlaps(csv_versions_by_ocp)

    payload = {
        "summary": {
            "ocp_versions_attempted": sorted(set(ocp_versions_attempted), key=version_key),
            "ocp_versions_scrape_ok": sorted(scrape_ok, key=version_key),
            "ocp_versions_checked": sorted(ocp_with_csv, key=version_key),
            "total_unique_csv_versions": len(all_csv),
            "csv_versions_with_overlaps": len(overlaps),
        },
        "scrape_report": scrape_report,
        "csv_versions_by_ocp": csv_versions_by_ocp,
        "overlaps": overlaps,
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    changed = True
    if os.path.isfile(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        changed = existing != payload

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")

    msg = "Wrote %s (%d OCP tags, %d unique operator CSVs)" % (
        output_path,
        len(ocp_versions_attempted),
        len(all_csv),
    )
    return {"changed": changed, "msg": msg}


def main():
    module = AnsibleModule(
        argument_spec=dict(
            scrape_results=dict(type="list", elements="dict", required=True),
            output_path=dict(type="path", required=True),
            package_name=dict(type="str", default="ansible-automation-platform-operator"),
        ),
        supports_check_mode=False,
    )
    module.exit_json(**run(module))


if __name__ == "__main__":
    main()
