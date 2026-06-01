# -*- coding: utf-8 -*-
# CSV matrix read/update helpers for aap_csv_update module.

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import csv
import os
import re
import shutil
from datetime import datetime

from ansible.module_utils.aap_operator_index import (
    extract_csv_versions_from_dir,
    extract_operator_index,
)


def extract_timestamp(version):
    match = re.search(r"-0\.(\d+)$", version)
    return int(match.group(1)) if match else None


def find_matching_version(target_version, other_versions, threshold=10000):
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
    if not os.path.isfile(csv_path):
        return []
    rows = []
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            rows.append(row)
    return rows


def get_column_value(row, col_index):
    if col_index < 1 or col_index > len(row):
        return ""
    return row[col_index - 1].strip().strip('"')


def get_existing_versions(rows, col_index):
    versions = set()
    for row in rows[1:]:
        val = get_column_value(row, col_index)
        if val and val.startswith("aap-operator."):
            versions.add(val)
    return versions


def find_row_with_version(rows, version, col_index):
    for i, row in enumerate(rows[1:], start=2):
        if get_column_value(row, col_index) == version:
            return i
    return None


def get_existing_ocp_support(rows, openshift_col, cluster_col, namespace_col):
    for row in rows[1:]:
        ocp_val = get_column_value(row, openshift_col)
        cluster_val = get_column_value(row, cluster_col)
        ns_val = get_column_value(row, namespace_col)
        if (
            ocp_val
            and re.match(r"^\d+\.\d+-\d+\.\d+$", ocp_val)
            and "aap-operator" not in ocp_val
            and (
                cluster_val.startswith("aap-operator")
                or ns_val.startswith("aap-operator")
            )
        ):
            return ocp_val
    return ""


def write_csv_rows(csv_path, rows):
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def update_row_cell(rows, row_num, col_index, value):
    idx = row_num - 1
    if 0 <= idx < len(rows) and 1 <= col_index <= len(rows[idx]):
        rows[idx][col_index - 1] = value


def add_new_row(
    rows,
    total_cols,
    date_col,
    cluster_col,
    namespace_col,
    openshift_col,
    scope,
    version,
    today_date,
    existing_ocp,
):
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


def add_new_row_pair(
    rows,
    total_cols,
    date_col,
    cluster_col,
    namespace_col,
    openshift_col,
    namespace_version,
    cluster_version,
    today_date,
    existing_ocp,
):
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


def ensure_operator_index_dir(module, ocp_version, cache_dir, registry):
    """Return (index_dir, pulled_fresh, status). Caller must not delete cached dirs."""
    cache_sub = None
    if cache_dir:
        cache_dir = os.path.abspath(os.path.expanduser(cache_dir))
        cache_sub = os.path.join(cache_dir, "ocp-%s" % ocp_version)
        if os.path.isdir(cache_sub):
            for root, _, files in os.walk(cache_sub):
                if any(f.endswith(".json") for f in files):
                    return cache_sub, False, "cached"

    tmp_dir, status = extract_operator_index(module, ocp_version, registry=registry)
    if tmp_dir is None:
        return None, True, status

    if cache_sub:
        if os.path.isdir(cache_sub):
            shutil.rmtree(cache_sub, ignore_errors=True)
        shutil.copytree(tmp_dir, cache_sub)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return cache_sub, True, status

    return tmp_dir, True, status


def update_csv_for_aap(
    module,
    index_dir,
    data_dir,
    columns,
    aap_version,
    namespace_channel,
    cluster_channel,
    package_name,
):
    """Update one AAP_*.csv from an already-extracted operator index directory."""
    cluster_col = int(columns.get("cluster_scoped", 2))
    namespace_col = int(columns.get("namespace_scoped", 3))
    date_col = int(columns.get("release_date", 1))
    openshift_col = int(columns.get("openshift_support", 4))

    aap_file_version = aap_version.replace(".", "")
    csv_path = os.path.join(data_dir, "AAP_%s.csv" % aap_file_version)
    if not os.path.isfile(csv_path):
        return {
            "changed": False,
            "rows_added": 0,
            "rows_updated": 0,
            "csv_path": csv_path,
            "skip_reason": "csv_not_found",
        }

    version_pattern = re.compile(
        r"^aap-operator\.v[\d]+\.[\d]+\.[\d]+-[\d]+\.[\d]+$"
    )
    namespace_versions, _, _ = extract_csv_versions_from_dir(
        index_dir, package_name, channel=namespace_channel
    )
    cluster_versions, _, _ = extract_csv_versions_from_dir(
        index_dir, package_name, channel=cluster_channel
    )
    namespace_versions = [v for v in namespace_versions if version_pattern.match(v)]
    cluster_versions = [v for v in cluster_versions if version_pattern.match(v)]

    if not namespace_versions and not cluster_versions:
        return {
            "changed": False,
            "rows_added": 0,
            "rows_updated": 0,
            "csv_path": csv_path,
            "skip_reason": "no_versions_in_index",
        }

    rows = read_csv_rows(csv_path)
    if not rows:
        return {
            "changed": False,
            "rows_added": 0,
            "rows_updated": 0,
            "csv_path": csv_path,
            "skip_reason": "empty_csv",
        }

    today_date = datetime.now().strftime("%B %d, %Y")
    total_cols = len(rows[0])
    existing_ns = get_existing_versions(rows, namespace_col)
    existing_cl = get_existing_versions(rows, cluster_col)
    existing_ocp = get_existing_ocp_support(
        rows, openshift_col, cluster_col, namespace_col
    )

    paired_cluster = set()
    changed = False
    rows_added = 0
    rows_updated = 0

    for ns_version in namespace_versions:
        if ns_version in existing_ns:
            continue
        matching_cl = find_matching_version(ns_version, cluster_versions)
        if matching_cl:
            if matching_cl in existing_cl:
                row_num = find_row_with_version(rows, matching_cl, cluster_col)
                if row_num:
                    update_row_cell(rows, row_num, namespace_col, ns_version)
                    if not get_column_value(rows[row_num - 1], date_col):
                        update_row_cell(rows, row_num, date_col, today_date)
                    changed = True
                    rows_updated += 1
            else:
                add_new_row_pair(
                    rows,
                    total_cols,
                    date_col,
                    cluster_col,
                    namespace_col,
                    openshift_col,
                    ns_version,
                    matching_cl,
                    today_date,
                    existing_ocp,
                )
                paired_cluster.add(matching_cl)
                changed = True
                rows_added += 1
        else:
            add_new_row(
                rows,
                total_cols,
                date_col,
                cluster_col,
                namespace_col,
                openshift_col,
                "namespace",
                ns_version,
                today_date,
                existing_ocp,
            )
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
                if not get_column_value(rows[row_num - 1], date_col):
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
            add_new_row(
                rows,
                total_cols,
                date_col,
                cluster_col,
                namespace_col,
                openshift_col,
                "cluster",
                cl_version,
                today_date,
                existing_ocp,
            )
            changed = True
            rows_added += 1

    if changed:
        write_csv_rows(csv_path, rows)

    return {
        "changed": changed,
        "rows_added": rows_added,
        "rows_updated": rows_updated,
        "csv_path": csv_path,
        "skip_reason": None,
    }
