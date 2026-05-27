#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: aap_operator_index_scrape
short_description: Scrape operator CSV names from one Red Hat operator index OCP tag
description:
  - Pulls one redhat-operator-index image and records operator CSV bundles in that tag.
  - Use in an Ansible loop (one OCP per task) for live progress; assemble with aap_operator_index_overlaps_write.
version_added: "1.0.0"
options:
  ocp_version:
    description: OCP index tag to scan (e.g. 4.18).
    required: true
    type: str
  package_name:
    description: OLM package name in the operator index.
    required: false
    type: str
    default: ansible-automation-platform-operator
  registry:
    description: Container registry host (without path).
    required: false
    type: str
    default: registry.redhat.io
author:
  - lennysh
'''

EXAMPLES = r'''
- name: Scrape operator index for each OCP version
  aap_operator_index_scrape:
    ocp_version: "{{ item }}"
    package_name: "{{ aap_matrix_operator_package }}"
    registry: "{{ aap_matrix_registry }}"
  loop: "{{ aap_matrix_operator_index_ocp_versions }}"
  loop_control:
    label: "OCP {{ item }}"
'''

RETURN = r'''
msg:
  description: Human-readable result shown in Ansible task output.
  type: str
ocp_version:
  description: OCP index tag scraped.
  type: str
status:
  description: Scrape status for this tag.
  type: str
csv_versions:
  description: Operator CSV bundle names found in this index.
  type: list
unique_operator_csv_count:
  description: Number of unique operator CSV names.
  type: int
scrape_report:
  description: Per-OCP scrape metadata for overlaps JSON assembly.
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule

from ansible.module_utils.aap_operator_index import scrape_all_csv_versions


def run(module):
    ocp_version = str(module.params["ocp_version"]).strip()
    package_name = module.params["package_name"]
    registry = module.params["registry"]

    if not ocp_version:
        module.fail_json(msg="ocp_version must be a non-empty string")

    result = scrape_all_csv_versions(
        module, ocp_version, package_name, registry=registry
    )
    csv_versions = result["csv_versions"]
    status = result["status"]
    count = len(csv_versions)

    msg = "OCP %s: status=%s, operator_csv_count=%d" % (ocp_version, status, count)
    if status != "ok":
        module.warn(msg)

    scrape_report = {
        "status": status,
        "config_json_files": result["config_json_files"],
        "olm_channel_objects_merged": result["olm_channel_objects_merged"],
        "unique_operator_csv_count": result["unique_operator_csv_count"],
    }

    return {
        "changed": False,
        "msg": msg,
        "ocp_version": ocp_version,
        "status": status,
        "csv_versions": csv_versions,
        "unique_operator_csv_count": count,
        "scrape_report": scrape_report,
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            ocp_version=dict(type="str", required=True),
            package_name=dict(type="str", default="ansible-automation-platform-operator"),
            registry=dict(type="str", default="registry.redhat.io"),
        ),
        supports_check_mode=False,
    )
    module.exit_json(**run(module))


if __name__ == "__main__":
    main()
