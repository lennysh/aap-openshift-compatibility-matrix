#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: aap_csv_update
short_description: Update one AAP matrix CSV from the operator index for one AAP stream
description:
  - Pulls (or reuses cached) the operator index for the first available OCP tag in
    ocp_versions and updates one AAP_*.csv file.
  - Use in an Ansible loop over aap_matrix_aap_streams for live progress.
version_added: "1.0.0"
options:
  ocp_versions:
    description:
      - OCP index tags to read the channels from, tried in order (newest first).
      - A tag whose index is not published yet is skipped; any other pull failure fails the task.
    required: true
    type: list
    elements: str
  aap_version:
    description: AAP version (e.g. 2.4) matching AAP_24.csv.
    required: true
    type: str
  namespace_channel:
    description: OLM channel name for namespace-scoped operator.
    required: true
    type: str
  cluster_channel:
    description: OLM channel name for cluster-scoped operator (empty if the stream has none).
    required: false
    type: str
    default: ""
  create_missing_csv:
    description: Create AAP_<ver>.csv (header from the newest existing CSV) if it does not exist.
    required: false
    type: bool
    default: false
  columns:
    description: Column indices for the matrix CSV.
    required: true
    type: dict
  data_dir:
    description: Directory containing AAP_*.csv files.
    required: true
    type: path
  index_cache_dir:
    description: Cache extracted index configs per OCP between loop iterations.
    required: false
    type: path
  package_name:
    description: OLM package name in the operator index.
    required: false
    type: str
    default: ansible-automation-platform-operator
  registry:
    description: Container registry host.
    required: false
    type: str
    default: registry.redhat.io
author:
  - lennysh
'''

EXAMPLES = r'''
- name: Update AAP CSV versions from operator index
  aap_csv_update:
    ocp_versions: "{{ item.ocp_versions }}"
    aap_version: "{{ item.aap_version }}"
    namespace_channel: "{{ item.namespace_channel }}"
    cluster_channel: "{{ item.cluster_channel }}"
    create_missing_csv: true
    columns: "{{ aap_matrix_columns }}"
    data_dir: "{{ aap_matrix_data_dir }}"
    index_cache_dir: "{{ aap_matrix_index_cache_dir }}"
  loop: "{{ aap_matrix_aap_streams }}"
  loop_control:
    label: "AAP {{ item.aap_version }}"
'''

RETURN = r'''
msg:
  description: Human-readable result for Ansible task output.
  type: str
changed:
  description: Whether the CSV file was modified.
  type: bool
rows_added:
  description: Rows added to the CSV.
  type: int
rows_updated:
  description: Rows updated in the CSV.
  type: int
index_cache_hit:
  description: True if operator index was reused from cache for this OCP.
  type: bool
ocp_version:
  description: OCP index tag actually used.
  type: str
csv_created:
  description: True if the CSV file was created by this run.
  type: bool
'''

import os
import shutil

from ansible.module_utils.basic import AnsibleModule

from ansible.module_utils.aap_csv_matrix import (
    ensure_operator_index_dir,
    update_csv_for_aap,
)


def run(module):
    ocp_versions = [str(v).strip() for v in module.params["ocp_versions"] or [] if str(v).strip()]
    aap_version = str(module.params["aap_version"]).strip()
    namespace_channel = str(module.params["namespace_channel"]).strip()
    cluster_channel = str(module.params["cluster_channel"] or "").strip()
    create_missing_csv = module.params["create_missing_csv"]
    columns = module.params["columns"] or {}
    package_name = module.params["package_name"]
    registry = module.params["registry"]

    data_dir = os.path.abspath(os.path.expanduser(module.params["data_dir"]))
    cache_dir = module.params.get("index_cache_dir")
    if cache_dir:
        cache_dir = os.path.abspath(os.path.expanduser(cache_dir))

    if not ocp_versions or not aap_version:
        module.fail_json(msg="ocp_versions and aap_version are required")
    if not namespace_channel:
        module.fail_json(msg="namespace_channel is required for AAP %s" % aap_version)

    # Read EVERY candidate tag and merge, rather than stopping at the first one that
    # pulls. Discovery lists a tag if any bundle of the stream ever shipped there, so
    # the newest tag can hold a single stale bundle (e.g. AAP 2.6 on 4.23 has only a
    # Sept build) while new releases land only on older tags. update_csv_for_aap skips
    # versions already in the CSV, so applying it once per tag is idempotent.
    not_published = []
    used = []
    cache_hit = True
    pulled_any = False
    result = None
    totals = {"changed": False, "rows_added": 0, "rows_updated": 0, "csv_created": False}
    for tag in ocp_versions:
        index_dir, pulled, index_status = ensure_operator_index_dir(
            module, tag, cache_dir, registry, package_name
        )
        if index_dir is None:
            if index_status == "tag_not_found":
                not_published.append(tag)
                continue
            module.fail_json(
                msg="Failed to extract operator index for OCP %s (status=%s)"
                % (tag, index_status)
            )
        try:
            r = update_csv_for_aap(
                module,
                index_dir,
                data_dir,
                columns,
                aap_version,
                namespace_channel,
                cluster_channel,
                package_name,
                create_missing_csv=create_missing_csv,
            )
        finally:
            if not cache_dir:
                shutil.rmtree(index_dir, ignore_errors=True)
        used.append(tag)
        cache_hit = cache_hit and index_status == "cached"
        pulled_any = pulled_any or pulled
        if r.get("skip_reason") == "csv_not_found":
            result = r
            break  # same answer for every tag
        if r.get("skip_reason") is None:
            for k in ("changed", "csv_created"):
                totals[k] = totals[k] or bool(r.get(k))
            for k in ("rows_added", "rows_updated"):
                totals[k] += r[k]
            result = dict(r, **totals)
        elif result is None or result.get("skip_reason"):
            result = r  # keep a skip reason only if no tag produced a real update

    if not used:
        module.fail_json(
            msg="None of the operator index tags for AAP %s are published: %s"
            % (aap_version, ", ".join(not_published))
        )
    ocp_version = "+".join(used)
    pulled = pulled_any

    csv_name = "AAP_%s.csv" % aap_version.replace(".", "")
    skip = result.get("skip_reason")
    if skip == "csv_not_found":
        msg = "OCP %s AAP %s: skipped (%s not found)" % (ocp_version, aap_version, csv_name)
        module.warn(msg)
    elif skip == "no_versions_in_index":
        msg = "OCP %s AAP %s: no operator CSVs in index channels" % (ocp_version, aap_version)
    elif skip == "empty_csv":
        msg = "OCP %s AAP %s: %s is empty" % (ocp_version, aap_version, csv_name)
    elif skip == "no_template_csv":
        msg = "OCP %s AAP %s: cannot create %s (no existing AAP_*.csv to copy the header from)" % (
            ocp_version, aap_version, csv_name)
        module.warn(msg)
    elif result["changed"]:
        msg = (
            ("OCP %s AAP %s: CREATED %s (+%d rows, ~%d rows)%s" if result.get("csv_created")
             else "OCP %s AAP %s: updated %s (+%d rows, ~%d rows)%s")
            % (
                ocp_version,
                aap_version,
                csv_name,
                result["rows_added"],
                result["rows_updated"],
                " [index cached]" if cache_hit else "",
            )
        )
    else:
        msg = "OCP %s AAP %s: %s up to date%s" % (
            ocp_version,
            aap_version,
            csv_name,
            " [index cached]" if cache_hit else "",
        )

    return {
        "changed": result["changed"],
        "msg": msg,
        "rows_added": result["rows_added"],
        "rows_updated": result["rows_updated"],
        "index_cache_hit": cache_hit,
        "index_pulled": pulled and not cache_hit,
        "ocp_version": ocp_version,
        "ocp_versions_not_published": not_published,
        "aap_version": aap_version,
        "csv_created": bool(result.get("csv_created")),
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            ocp_versions=dict(type="list", elements="str", required=True),
            aap_version=dict(type="str", required=True),
            namespace_channel=dict(type="str", required=True),
            cluster_channel=dict(type="str", default=""),
            create_missing_csv=dict(type="bool", default=False),
            columns=dict(type="dict", required=True),
            data_dir=dict(type="path", required=True),
            index_cache_dir=dict(type="path", required=False),
            package_name=dict(type="str", default="ansible-automation-platform-operator"),
            registry=dict(type="str", default="registry.redhat.io"),
        ),
        supports_check_mode=False,
    )
    module.exit_json(**run(module))


if __name__ == "__main__":
    main()
