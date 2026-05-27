#!/usr/bin/python3
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, AAP CSV Update Module
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: aap_csv_update
short_description: Check and update AAP operator CSV versions against compatibility matrix
description:
  - Pulls Red Hat operator index images via podman
  - Extracts CSV versions from namespace and cluster-scoped channels
  - Pairs versions by timestamp and updates CSV files with missing entries
version_added: "1.0.0"
options:
  columns:
    description: Column indices (release_date, cluster_scoped, namespace_scoped, openshift_support). Used when loops is provided.
    required: false
    type: dict
  loops:
    description: List of loop entries (ocp_version, channel, aap_version, scope). When provided, config_file is ignored.
    required: false
    type: list
    elements: dict
  config_file:
    description: Path to check-versions.json config file. Ignored when loops is provided.
    required: false
    type: path
    default: "scripts/check-versions.json"
  data_dir:
    description: Directory containing AAP_*.csv files
    required: false
    type: path
    default: "data"
  registry:
    description: Container registry for operator index (for podman login)
    required: false
    type: str
    default: "registry.redhat.io"
author:
  - AAP Compatibility Matrix
'''

EXAMPLES = r'''
- name: Update AAP CSV versions using vars file
  aap_csv_update:
    columns: "{{ columns }}"
    loops: "{{ loops }}"
    data_dir: "{{ playbook_dir }}/../data"

- name: Update AAP CSV versions using config file
  aap_csv_update:
    config_file: "{{ playbook_dir }}/check-versions.json"
    data_dir: "{{ playbook_dir }}/../data"
'''

RETURN = r'''
changed:
  description: Whether any CSV files were modified
  type: bool
  returned: always
messages:
  description: List of status messages from the update process
  type: list
  returned: always
rows_added:
  description: Number of rows added to CSV files
  type: int
  returned: always
rows_updated:
  description: Number of rows updated in CSV files
  type: int
  returned: always
'''

import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime

from ansible.module_utils.basic import AnsibleModule


def run_command(module, cmd, check_rc=True, capture=True):
    """Run a command and return (rc, stdout, stderr)."""
    result = subprocess.run(
        cmd,
        capture_output=capture,
        shell=isinstance(cmd, str),
        text=True,
    )
    if check_rc and result.returncode != 0:
        module.fail_json(msg="Command failed: %s" % cmd, stderr=result.stderr)
    return result.returncode, result.stdout, result.stderr


def extract_timestamp(version):
    """Extract timestamp from version string (e.g., aap-operator.v2.4.0-0.1749069319 -> 1749069319)."""
    match = re.search(r'-0\.(\d+)$', version)
    return int(match.group(1)) if match else None


def find_matching_version(target_version, other_versions, threshold=10000):
    """Find version with closest timestamp in other_versions."""
    target_ts = extract_timestamp(target_version)
    if not target_ts:
        return None

    best_match = None
    best_diff = threshold

    for other in other_versions:
        other_ts = extract_timestamp(other)
        if not other_ts:
            continue
        diff = abs(target_ts - other_ts)
        if diff < best_diff:
            best_diff = diff
            best_match = other

    return best_match


def read_csv_rows(csv_path):
    """Read CSV file and return list of rows (each row is list of values)."""
    if not os.path.isfile(csv_path):
        return []
    rows = []
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
    return rows


def get_column_value(row, col_index):
    """Get value from row at 1-based column index, stripping quotes."""
    if col_index < 1 or col_index > len(row):
        return ""
    val = row[col_index - 1].strip().strip('"')
    return val


def get_existing_versions(rows, col_index):
    """Get set of existing versions from column (1-based)."""
    versions = set()
    for row in rows[1:]:  # skip header
        val = get_column_value(row, col_index)
        if val and val.startswith('aap-operator.'):
            versions.add(val)
    return versions


def find_row_with_version(rows, version, col_index):
    """Find 1-based row number containing version in column."""
    for i, row in enumerate(rows[1:], start=2):
        if get_column_value(row, col_index) == version:
            return i
    return None


def get_existing_ocp_support(rows, openshift_col, cluster_col, namespace_col):
    """Get OpenShift support value from a valid row."""
    for row in rows[1:]:
        ocp_val = get_column_value(row, openshift_col)
        cluster_val = get_column_value(row, cluster_col)
        ns_val = get_column_value(row, namespace_col)
        if (ocp_val and re.match(r'^\d+\.\d+-\d+\.\d+$', ocp_val) and
                'aap-operator' not in ocp_val and
                (cluster_val.startswith('aap-operator') or ns_val.startswith('aap-operator'))):
            return ocp_val
    return ""


def write_csv_rows(csv_path, rows):
    """Write rows to CSV file."""
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def update_row_cell(rows, row_num, col_index, value):
    """Update a cell in the rows (1-based indices)."""
    idx = row_num - 1
    if 0 <= idx < len(rows) and 1 <= col_index <= len(rows[idx]):
        rows[idx][col_index - 1] = value


def add_new_row(rows, total_cols, date_col, cluster_col, namespace_col, openshift_col,
                scope, version, today_date, existing_ocp):
    """Build a new row and append to rows."""
    new_row = [""] * total_cols
    for i in range(1, total_cols + 1):
        if i == date_col:
            new_row[i - 1] = today_date
        elif i == cluster_col and scope == "cluster":
            new_row[i - 1] = version
        elif i == namespace_col and scope == "namespace":
            new_row[i - 1] = version
        elif i == openshift_col and existing_ocp:
            new_row[i - 1] = existing_ocp
    rows.append(new_row)


def add_new_row_pair(rows, total_cols, date_col, cluster_col, namespace_col, openshift_col,
                     namespace_version, cluster_version, today_date, existing_ocp):
    """Build a new row with both versions and append."""
    new_row = [""] * total_cols
    for i in range(1, total_cols + 1):
        if i == date_col:
            new_row[i - 1] = today_date
        elif i == cluster_col:
            new_row[i - 1] = cluster_version
        elif i == namespace_col:
            new_row[i - 1] = namespace_version
        elif i == openshift_col and existing_ocp:
            new_row[i - 1] = existing_ocp
    rows.append(new_row)


def extract_operator_index(module, ocp_version):
    """Extract operator index for OCP version. Returns temp dir path or None."""
    image = "registry.redhat.io/redhat/redhat-operator-index:v%s" % ocp_version
    tmp_dir = tempfile.mkdtemp()

    try:
        rc, _, err = run_command(module, ["podman", "pull", image], check_rc=False)
        if rc != 0:
            module.warn("Failed to pull image %s: %s" % (image, err))
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

        result = subprocess.run(["podman", "create", image], capture_output=True, text=True)
        if result.returncode != 0:
            module.warn("Failed to create container")
            subprocess.run(["podman", "rmi", image], capture_output=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

        container_id = result.stdout.strip()
        cp_result = subprocess.run(
            "podman cp %s:/configs - | tar -x -C %s" % (container_id, tmp_dir),
            shell=True, capture_output=True
        )
        subprocess.run(["podman", "rm", "-v", container_id], capture_output=True)

        if cp_result.returncode != 0:
            module.warn("Failed to extract configs")
            subprocess.run(["podman", "rmi", image], capture_output=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

        subprocess.run(["podman", "rmi", image], capture_output=True)
        return tmp_dir
    except Exception as e:
        module.warn("Error extracting operator index: %s" % str(e))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None


def extract_versions_from_dir(tmp_dir, channel, package_name="ansible-automation-platform-operator"):
    """Extract CSV versions for channel from extracted config directory."""
    versions = []
    for root, _, files in os.walk(tmp_dir):
        for f in files:
            if not f.endswith('.json'):
                continue
            path = os.path.join(root, f)
            try:
                with open(path, 'r') as fp:
                    content = fp.read()
                    # OLM index may have multiple JSON objects concatenated
                    decoder = json.JSONDecoder()
                    idx = 0
                    while idx < len(content):
                        obj, end = decoder.raw_decode(content[idx:])
                        idx += end
                        if (obj.get('schema') == 'olm.channel' and
                                obj.get('package') == package_name and
                                obj.get('name') == channel):
                            for entry in obj.get('entries', []):
                                name = entry.get('name', entry) if isinstance(entry, dict) else entry
                                if name and isinstance(name, str) and name.startswith('aap-operator.'):
                                    versions.append(name)
                        # Skip whitespace between objects
                        while idx < len(content) and content[idx] in ' \t\n\r':
                            idx += 1
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    return sorted(set(versions), key=lambda v: (extract_timestamp(v) or 0))


def run(module):
    """Main module logic."""
    data_dir = os.path.expanduser(module.params['data_dir'])
    if not os.path.isabs(data_dir):
        data_dir = os.path.join(os.getcwd(), data_dir)

    loops = module.params.get('loops') or []
    if loops:
        columns = module.params.get('columns') or {}
    else:
        config_file = os.path.expanduser(module.params['config_file'])
        if not os.path.isabs(config_file):
            config_file = os.path.join(os.getcwd(), config_file)
        if not os.path.isfile(config_file):
            module.fail_json(msg="Config file not found: %s (or provide loops in vars)" % config_file)
        with open(config_file, 'r') as f:
            config = json.load(f)
        columns = config.get('columns', {})
        loops = config.get('loops', [])

    cluster_col = int(columns.get('cluster_scoped', 2))
    namespace_col = int(columns.get('namespace_scoped', 3))
    date_col = int(columns.get('release_date', 1))
    openshift_col = int(columns.get('openshift_support', 4))
    if not loops:
        return {"changed": False, "messages": ["No loops in config"], "rows_added": 0, "rows_updated": 0}

    today_date = datetime.now().strftime("%B %d, %Y")
    version_pattern = re.compile(r'^aap-operator\.v[\d]+\.[\d]+\.[\d]+-[\d]+\.[\d]+$')

    rows_added = 0
    rows_updated = 0
    messages = []

    ocp_versions = sorted(set(l.get('ocp_version') for l in loops if l.get('ocp_version')))

    for ocp_version in ocp_versions:
        messages.append("Processing OCP version: %s" % ocp_version)
        tmp_dir = extract_operator_index(module, ocp_version)
        if not tmp_dir:
            messages.append("  Failed to extract operator index for OCP %s" % ocp_version)
            continue

        try:
            aap_pairs = {}
            for loop in loops:
                if loop.get('ocp_version') != ocp_version:
                    continue
                aap = loop.get('aap_version', '')
                scope = loop.get('scope', '')
                channel = loop.get('channel', '')
                if aap not in aap_pairs:
                    aap_pairs[aap] = {}
                aap_pairs[aap][scope] = channel

            for aap_version_input, channels in aap_pairs.items():
                ns_channel = channels.get('namespace')
                cl_channel = channels.get('cluster')
                if not ns_channel or not cl_channel:
                    messages.append("  Skipping AAP %s: missing namespace or cluster channel" % aap_version_input)
                    continue

                aap_version = aap_version_input.replace('.', '')
                csv_path = os.path.join(data_dir, "AAP_%s.csv" % aap_version)
                if not os.path.isfile(csv_path):
                    messages.append("  CSV file not found: %s" % csv_path)
                    continue

                namespace_versions = [v for v in extract_versions_from_dir(tmp_dir, ns_channel)
                                     if version_pattern.match(v)]
                cluster_versions = [v for v in extract_versions_from_dir(tmp_dir, cl_channel)
                                   if version_pattern.match(v)]

                if not namespace_versions and not cluster_versions:
                    continue

                rows = read_csv_rows(csv_path)
                if not rows:
                    continue

                total_cols = len(rows[0])
                existing_ns = get_existing_versions(rows, namespace_col)
                existing_cl = get_existing_versions(rows, cluster_col)
                existing_ocp = get_existing_ocp_support(rows, openshift_col, cluster_col, namespace_col)

                paired_cluster = set()
                changed = False

                for ns_version in namespace_versions:
                    if ns_version in existing_ns:
                        continue
                    matching_cl = find_matching_version(ns_version, cluster_versions)
                    if matching_cl:
                        if matching_cl in existing_cl:
                            row_num = find_row_with_version(rows, matching_cl, cluster_col)
                            if row_num:
                                update_row_cell(rows, row_num, namespace_col, ns_version)
                                date_val = get_column_value(rows[row_num - 1], date_col)
                                if not date_val:
                                    update_row_cell(rows, row_num, date_col, today_date)
                                changed = True
                                rows_updated += 1
                        else:
                            add_new_row_pair(rows, total_cols, date_col, cluster_col, namespace_col,
                                            openshift_col, ns_version, matching_cl, today_date, existing_ocp)
                            paired_cluster.add(matching_cl)
                            changed = True
                            rows_added += 1
                    else:
                        add_new_row(rows, total_cols, date_col, cluster_col, namespace_col, openshift_col,
                                   "namespace", ns_version, today_date, existing_ocp)
                        changed = True
                        rows_added += 1

                for cl_version in cluster_versions:
                    if cl_version in existing_cl or cl_version in paired_cluster:
                        continue
                    matching_ns = find_matching_version(cl_version, namespace_versions)
                    if matching_ns and matching_ns in existing_ns:
                        row_num = find_row_with_version(rows, matching_ns, namespace_col)
                        if row_num:
                            update_row_cell(rows, row_num, cluster_col, cl_version)
                            date_val = get_column_value(rows[row_num - 1], date_col)
                            if not date_val:
                                update_row_cell(rows, row_num, date_col, today_date)
                        changed = True
                        rows_updated += 1
                    elif matching_ns:
                        row_num = find_row_with_version(rows, matching_ns, namespace_col)
                        if row_num:
                            update_row_cell(rows, row_num, cluster_col, cl_version)
                            changed = True
                            rows_updated += 1
                    else:
                        add_new_row(rows, total_cols, date_col, cluster_col, namespace_col, openshift_col,
                                   "cluster", cl_version, today_date, existing_ocp)
                        changed = True
                        rows_added += 1

                if changed:
                    write_csv_rows(csv_path, rows)
                    messages.append("  Updated AAP_%s.csv" % aap_version)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "changed": rows_added > 0 or rows_updated > 0,
        "messages": messages,
        "rows_added": rows_added,
        "rows_updated": rows_updated,
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            columns=dict(type='dict', required=False),
            loops=dict(type='list', elements='dict', required=False),
            config_file=dict(type='path', default='scripts/check-versions.json'),
            data_dir=dict(type='path', default='data'),
            registry=dict(type='str', default='registry.redhat.io'),
        ),
        supports_check_mode=False,
    )
    result = run(module)
    module.exit_json(**result)


if __name__ == '__main__':
    main()
