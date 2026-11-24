# -*- coding: utf-8 -*-
# CSV matrix read/update helpers for aap_csv_update module.

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import csv
import os
import re
import shutil
from datetime import datetime, timezone

from ansible.module_utils.aap_operator_index import extract_operator_index


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


def _parse_utc(value):
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def release_date(names, first_seen, now=None):
    """Release Date for a newly added row: the earliest the build was seen, as a UTC date.

    "Seen" is the Red Hat catalog API's first record of any of the row's bundles, or now
    (this run just found it in an index), whichever is earlier. Using the run's clock alone
    stamped rows a day late whenever the run happened after midnight UTC or hours late
    (7 of the automation-added rows so far). The catalog record can trail the index by a few
    minutes, which is why "now" stays a candidate.
    """
    now = now or datetime.now(timezone.utc)
    seen = [t for t in (_parse_utc((first_seen or {}).get(n)) for n in names if n) if t]
    return min(seen + [now]).strftime("%B %d, %Y")


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


def template_header(data_dir):
    """Header row of the newest existing AAP_<digits>.csv in data_dir (or None)."""
    best = None
    for name in os.listdir(data_dir):
        m = re.match(r"^AAP_(\d)(\d+)\.csv$", name)
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)))
        if best is None or key > best[0]:
            best = (key, name)
    if best is None:
        return None
    rows = read_csv_rows(os.path.join(data_dir, best[1]))
    return rows[0] if rows else None


def write_csv_rows(csv_path, rows):
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def update_row_cell(rows, row_num, col_index, value):
    idx = row_num - 1
    if 0 <= idx < len(rows) and 1 <= col_index <= len(rows[idx]):
        rows[idx][col_index - 1] = value


def _row_timestamp(row, cluster_col, namespace_col):
    stamps = [extract_timestamp(get_column_value(row, c)) for c in (cluster_col, namespace_col)]
    stamps = [t for t in stamps if t]
    return max(stamps) if stamps else None


def insert_row_in_order(module, rows, new_row, version, cluster_col, namespace_col):
    """Append new_row, unless it is older than an existing row (a backfilled build).

    Then insert it before the first row built after it, so the CSV stays chronological,
    and warn: reading every index means a historical build that was never recorded would
    otherwise land at the bottom as if it were the newest release.
    """
    ts = extract_timestamp(version)
    if ts:
        for i in range(1, len(rows)):
            row_ts = _row_timestamp(rows[i], cluster_col, namespace_col)
            if row_ts and row_ts > ts:
                module.warn(
                    "%s is older than existing rows; inserted in build order at line %d "
                    "instead of appended" % (version, i + 1)
                )
                rows.insert(i, new_row)
                return
    rows.append(new_row)


def add_new_row(
    module,
    rows,
    total_cols,
    date_col,
    cluster_col,
    namespace_col,
    openshift_col,
    scope,
    version,
    row_date,
    existing_ocp,
):
    new_row = [""] * total_cols
    for i in range(1, total_cols + 1):
        if i == date_col:
            new_row[i - 1] = row_date
        elif i == cluster_col and scope == "cluster":
            new_row[i - 1] = version
        elif i == namespace_col and scope == "namespace":
            new_row[i - 1] = version
        elif i == openshift_col and existing_ocp:
            new_row[i - 1] = existing_ocp
    insert_row_in_order(module, rows, new_row, version, cluster_col, namespace_col)


def add_new_row_pair(
    module,
    rows,
    total_cols,
    date_col,
    cluster_col,
    namespace_col,
    openshift_col,
    namespace_version,
    cluster_version,
    row_date,
    existing_ocp,
):
    new_row = [""] * total_cols
    for i in range(1, total_cols + 1):
        if i == date_col:
            new_row[i - 1] = row_date
        elif i == cluster_col:
            new_row[i - 1] = cluster_version
        elif i == namespace_col:
            new_row[i - 1] = namespace_version
        elif i == openshift_col and existing_ocp:
            new_row[i - 1] = existing_ocp
    insert_row_in_order(module, rows, new_row, namespace_version, cluster_col, namespace_col)


def ensure_operator_index_dir(module, ocp_version, cache_dir, registry, package_name=None):
    """Return (index_dir, pulled_fresh, status). Caller must not delete cached dirs."""
    cache_sub = None
    if cache_dir:
        cache_dir = os.path.abspath(os.path.expanduser(cache_dir))
        cache_sub = os.path.join(cache_dir, "ocp-%s" % ocp_version)
        if os.path.isdir(cache_sub):
            for root, _, files in os.walk(cache_sub):
                if any(f.endswith(".json") for f in files):
                    return cache_sub, False, "cached"

    tmp_dir, status = extract_operator_index(
        module, ocp_version, registry=registry, package_name=package_name
    )
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
    data_dir,
    columns,
    aap_version,
    namespace_versions,
    cluster_versions,
    create_missing_csv=False,
    release_dates=None,
):
    """Update one AAP_*.csv from the operator CSV names found in the stream's channels.

    namespace_versions / cluster_versions are the bundle names from the namespace- and
    cluster-scoped channels, merged across every OCP index read. If the CSV does not exist
    and create_missing_csv is set, it is created (header copied from the newest existing
    AAP_*.csv) once the channels have builds. release_dates maps operator CSV names to the
    catalog API's first-seen time (see release_date).
    """
    cluster_col = int(columns.get("cluster_scoped", 2))
    namespace_col = int(columns.get("namespace_scoped", 3))
    date_col = int(columns.get("release_date", 1))
    openshift_col = int(columns.get("openshift_support", 4))

    aap_file_version = aap_version.replace(".", "")
    csv_path = os.path.join(data_dir, "AAP_%s.csv" % aap_file_version)
    csv_exists = os.path.isfile(csv_path)
    if not csv_exists and not create_missing_csv:
        return {
            "changed": False,
            "rows_added": 0,
            "rows_updated": 0,
            "csv_path": csv_path,
            "skip_reason": "csv_not_found",
            "csv_created": False,
        }

    version_pattern = re.compile(
        r"^aap-operator\.v[\d]+\.[\d]+\.[\d]+-[\d]+\.[\d]+$"
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
            "csv_created": False,
        }

    csv_created = False
    if not csv_exists:
        header = template_header(data_dir)
        if not header:
            return {
                "changed": False,
                "rows_added": 0,
                "rows_updated": 0,
                "csv_path": csv_path,
                "skip_reason": "no_template_csv",
                "csv_created": False,
            }
        write_csv_rows(csv_path, [header])
        csv_created = True

    rows = read_csv_rows(csv_path)
    if not rows:
        return {
            "changed": False,
            "rows_added": 0,
            "rows_updated": 0,
            "csv_path": csv_path,
            "skip_reason": "empty_csv",
            "csv_created": False,
        }

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
                        update_row_cell(rows, row_num, date_col,
                                        release_date([ns_version, matching_cl], release_dates))
                    changed = True
                    rows_updated += 1
            else:
                add_new_row_pair(
                    module,
                    rows,
                    total_cols,
                    date_col,
                    cluster_col,
                    namespace_col,
                    openshift_col,
                    ns_version,
                    matching_cl,
                    release_date([ns_version, matching_cl], release_dates),
                    existing_ocp,
                )
                paired_cluster.add(matching_cl)
                changed = True
                rows_added += 1
        else:
            add_new_row(
                module,
                rows,
                total_cols,
                date_col,
                cluster_col,
                namespace_col,
                openshift_col,
                "namespace",
                ns_version,
                release_date([ns_version], release_dates),
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
                    update_row_cell(rows, row_num, date_col,
                                    release_date([cl_version, matching_ns], release_dates))
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
                module,
                rows,
                total_cols,
                date_col,
                cluster_col,
                namespace_col,
                openshift_col,
                "cluster",
                cl_version,
                release_date([cl_version], release_dates),
                existing_ocp,
            )
            changed = True
            rows_added += 1

    if changed:
        write_csv_rows(csv_path, rows)

    return {
        "changed": changed or csv_created,
        "rows_added": rows_added,
        "rows_updated": rows_updated,
        "csv_path": csv_path,
        "skip_reason": None,
        "csv_created": csv_created,
    }
