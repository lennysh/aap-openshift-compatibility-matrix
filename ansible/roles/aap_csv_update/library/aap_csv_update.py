#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: aap_csv_update
short_description: Update one AAP matrix CSV from the operator index for one AAP stream
description:
  - Reads the stream's channels from every OCP index tag in ocp_versions (pulled, or
    reused from the run cache), merges them, and updates one AAP_*.csv file.
  - Use in an Ansible loop over aap_matrix_aap_streams for live progress.
version_added: "1.0.0"
options:
  ocp_versions:
    description:
      - OCP index tags to read the channels from. All of them are read and merged, since a
        release can ship to any subset of them.
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
ocp_versions_read:
  description: OCP index tags whose channels were read and merged.
  type: list
ocp_versions_not_published:
  description: Listed OCP index tags whose index image isn't published yet (skipped).
  type: list
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
from ansible.module_utils.aap_operator_index import (
    extract_csv_versions_from_dir,
    version_key_from_csv,
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

    # Read the stream's channels from EVERY listed OCP index and merge them. Every tag is
    # scraped for OpenShift Support anyway (and prefetched into the run cache), and a new
    # release can land on any subset of them: e.g. the newest index carrying AAP 2.6 (4.23)
    # holds a single September build while new 2.6 releases ship only to 4.22 and older.
    namespace_versions = set()
    cluster_versions = set()
    used = []
    not_published = []
    for tag in ocp_versions:
        index_dir, _, index_status = ensure_operator_index_dir(
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
            namespace_versions.update(extract_csv_versions_from_dir(
                index_dir, package_name, channel=namespace_channel)[0])
            if cluster_channel:
                cluster_versions.update(extract_csv_versions_from_dir(
                    index_dir, package_name, channel=cluster_channel)[0])
        finally:
            if not cache_dir:
                shutil.rmtree(index_dir, ignore_errors=True)
        used.append(tag)

    if not used:
        module.fail_json(
            msg="None of the operator index tags for AAP %s are published: %s"
            % (aap_version, ", ".join(not_published))
        )

    result = update_csv_for_aap(
        module,
        data_dir,
        columns,
        aap_version,
        sorted(namespace_versions, key=version_key_from_csv),
        sorted(cluster_versions, key=version_key_from_csv),
        create_missing_csv=create_missing_csv,
    )
    where = "%d OCP tag%s" % (len(used), "" if len(used) == 1 else "s")

    csv_name = "AAP_%s.csv" % aap_version.replace(".", "")
    skip = result.get("skip_reason")
    if skip == "csv_not_found":
        msg = "AAP %s (%s): skipped (%s not found)" % (aap_version, where, csv_name)
        module.warn(msg)
    elif skip == "no_versions_in_index":
        msg = "AAP %s (%s): no operator CSVs in index channels" % (aap_version, where)
    elif skip == "empty_csv":
        msg = "AAP %s (%s): %s is empty" % (aap_version, where, csv_name)
    elif skip == "no_template_csv":
        msg = "AAP %s (%s): cannot create %s (no existing AAP_*.csv to copy the header from)" % (
            aap_version, where, csv_name)
        module.warn(msg)
    elif result["changed"]:
        msg = (
            ("AAP %s (%s): CREATED %s (+%d rows, ~%d rows)" if result.get("csv_created")
             else "AAP %s (%s): updated %s (+%d rows, ~%d rows)")
            % (aap_version, where, csv_name, result["rows_added"], result["rows_updated"])
        )
    else:
        msg = "AAP %s (%s): %s up to date" % (aap_version, where, csv_name)

    return {
        "changed": result["changed"],
        "msg": msg,
        "rows_added": result["rows_added"],
        "rows_updated": result["rows_updated"],
        "ocp_versions_read": used,
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
