#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: aap_csv_update
short_description: Update one AAP matrix CSV from operator index for a single OCP and AAP version
description:
  - Pulls (or reuses cached) operator index for one OCP tag and updates one AAP_*.csv file.
  - Use in an Ansible loop over namespace-scoped aap_matrix_loops entries for live progress.
version_added: "1.0.0"
options:
  ocp_version:
    description: OCP index tag (e.g. 4.18).
    required: true
    type: str
  aap_version:
    description: AAP version (e.g. 2.4) matching AAP_24.csv.
    required: true
    type: str
  namespace_channel:
    description: OLM channel name for namespace-scoped operator.
    required: true
    type: str
  cluster_channel:
    description: OLM channel name for cluster-scoped operator.
    required: true
    type: str
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
    ocp_version: "{{ item.ocp_version }}"
    aap_version: "{{ item.aap_version }}"
    namespace_channel: "{{ item.channel }}"
    cluster_channel: "{{ aap_csv_update_cluster_channel }}"
    columns: "{{ aap_matrix_columns }}"
    data_dir: "{{ aap_matrix_data_dir }}"
    index_cache_dir: "{{ aap_matrix_index_cache_dir }}"
  loop: "{{ aap_matrix_loops | selectattr('scope', 'equalto', 'namespace') | list }}"
  loop_control:
    label: "OCP {{ item.ocp_version }} AAP {{ item.aap_version }}"
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
'''

import os
import shutil

from ansible.module_utils.basic import AnsibleModule

from ansible.module_utils.aap_csv_matrix import (
    ensure_operator_index_dir,
    update_csv_for_aap,
)


def run(module):
    ocp_version = str(module.params["ocp_version"]).strip()
    aap_version = str(module.params["aap_version"]).strip()
    namespace_channel = str(module.params["namespace_channel"]).strip()
    cluster_channel = str(module.params["cluster_channel"]).strip()
    columns = module.params["columns"] or {}
    package_name = module.params["package_name"]
    registry = module.params["registry"]

    data_dir = os.path.abspath(os.path.expanduser(module.params["data_dir"]))
    cache_dir = module.params.get("index_cache_dir")
    if cache_dir:
        cache_dir = os.path.abspath(os.path.expanduser(cache_dir))

    if not ocp_version or not aap_version:
        module.fail_json(msg="ocp_version and aap_version are required")
    if not namespace_channel or not cluster_channel:
        module.fail_json(
            msg="namespace_channel and cluster_channel are required for AAP %s on OCP %s"
            % (aap_version, ocp_version)
        )

    index_dir, pulled, index_status = ensure_operator_index_dir(
        module, ocp_version, cache_dir, registry
    )
    cache_hit = index_status == "cached"
    cleanup_index = index_dir is not None and not cache_dir

    if index_dir is None:
        module.fail_json(
            msg="Failed to extract operator index for OCP %s (status=%s)"
            % (ocp_version, index_status)
        )

    try:
        result = update_csv_for_aap(
            module,
            index_dir,
            data_dir,
            columns,
            aap_version,
            namespace_channel,
            cluster_channel,
            package_name,
        )
    finally:
        if cleanup_index:
            shutil.rmtree(index_dir, ignore_errors=True)

    csv_name = "AAP_%s.csv" % aap_version.replace(".", "")
    skip = result.get("skip_reason")
    if skip == "csv_not_found":
        msg = "OCP %s AAP %s: skipped (%s not found)" % (ocp_version, aap_version, csv_name)
        module.warn(msg)
    elif skip == "no_versions_in_index":
        msg = "OCP %s AAP %s: no operator CSVs in index channels" % (ocp_version, aap_version)
    elif skip == "empty_csv":
        msg = "OCP %s AAP %s: %s is empty" % (ocp_version, aap_version, csv_name)
    elif result["changed"]:
        msg = (
            "OCP %s AAP %s: updated %s (+%d rows, ~%d rows)%s"
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
        "aap_version": aap_version,
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            ocp_version=dict(type="str", required=True),
            aap_version=dict(type="str", required=True),
            namespace_channel=dict(type="str", required=True),
            cluster_channel=dict(type="str", required=True),
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
