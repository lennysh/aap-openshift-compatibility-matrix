#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: aap_operator_index_prefetch
short_description: Pull several Red Hat operator index tags in parallel into the run cache
description:
  - Pulls each OCP index tag and extracts the package's catalog into index_cache_dir
    (the same layout aap_csv_update and aap_operator_index_scrape already read), several
    tags at a time. Those tasks then find every index cached instead of pulling them one
    by one.
  - Purely an accelerator. A tag that fails here (or isn't published yet) is simply not
    cached, and the later tasks pull it themselves exactly as they would without this
    module, so their success/failure behavior is unchanged.
  - Each extract is moved into place only once complete, so a failure mid-copy never
    leaves a partial extract that looks cached.
version_added: "1.2.0"
options:
  ocp_versions:
    description: OCP index tags to prefetch.
    required: true
    type: list
    elements: str
  index_cache_dir:
    description: Run cache directory (one ocp-<tag> subdirectory per index).
    required: true
    type: path
  package_name:
    description: OLM package whose catalog is extracted.
    type: str
    default: ansible-automation-platform-operator
  registry:
    description: Container registry host.
    type: str
    default: registry.redhat.io
  workers:
    description:
      - How many indexes to pull at once. Each in-flight index needs a few GB of container
        storage until it is extracted and removed.
    type: int
    default: 3
author:
  - lennysh
'''

RETURN = r'''
msg:
  description: One-line summary.
  type: str
results:
  description: Per-tag result (ocp_version, status, seconds, free_gb, line).
  type: list
not_cached:
  description: Tags that were not cached (later tasks pull these themselves).
  type: list
min_free_gb:
  description: Lowest free space seen on the container storage filesystem after a pull.
  type: float
'''

import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from ansible.module_utils.basic import AnsibleModule

from ansible.module_utils.aap_operator_index import (
    cached_index_dir,
    extract_operator_index,
)


def _graphroot():
    result = subprocess.run(
        ["podman", "info", "--format", "{{.Store.GraphRoot}}"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _free_gb(path):
    try:
        return round(shutil.disk_usage(path).free / 1e9, 1)
    except OSError:
        return None


def _fetch(module, tag, cache_dir, registry, package_name, disk_path):
    start = time.time()
    if cached_index_dir(cache_dir, tag):
        return {"ocp_version": tag, "status": "cached", "seconds": 0.0, "free_gb": None}

    tmp_dir, status = extract_operator_index(
        module, tag, registry=registry, package_name=package_name
    )
    entry = {
        "ocp_version": tag,
        "status": status,
        "seconds": round(time.time() - start, 1),
        "free_gb": _free_gb(disk_path),
    }
    if tmp_dir is None:
        return entry

    target = os.path.join(cache_dir, "ocp-%s" % tag)
    staging = target + ".partial"
    try:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.move(tmp_dir, staging)  # may cross filesystems (/tmp -> repo checkout)
        shutil.rmtree(target, ignore_errors=True)
        os.rename(staging, target)  # atomic, so readers never see a half-copied extract
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        entry["status"] = "cache_write_failed: %s" % exc
    return entry


def run(module):
    p = module.params
    tags = []
    for tag in p["ocp_versions"] or []:
        tag = str(tag).strip()
        if tag and tag not in tags:
            tags.append(tag)
    if not tags:
        return {"changed": False, "msg": "No OCP tags to prefetch", "results": [],
                "not_cached": [], "min_free_gb": None}

    cache_dir = os.path.abspath(os.path.expanduser(p["index_cache_dir"]))
    os.makedirs(cache_dir, exist_ok=True)
    workers = max(1, min(p["workers"], len(tags)))

    # Initialise rootless podman storage once, before several podman processes start at once
    subprocess.run(["podman", "info"], capture_output=True)
    disk_path = _graphroot() or cache_dir

    def fetch(tag):
        try:
            return _fetch(module, tag, cache_dir, p["registry"], p["package_name"], disk_path)
        except Exception as exc:  # noqa: BLE001 - never let one tag sink the others
            return {"ocp_version": tag, "status": "error: %s" % exc, "seconds": 0.0, "free_gb": None}

    start = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fetch, tags))
    elapsed = time.time() - start

    for r in results:
        free = "" if r["free_gb"] is None else ", %.1f GB free" % r["free_gb"]
        r["line"] = "OCP %s: %s in %.1fs%s" % (r["ocp_version"], r["status"], r["seconds"], free)

    cached = [r for r in results if r["status"] in ("ok", "cached")]
    not_cached = [r["ocp_version"] for r in results if r["status"] not in ("ok", "cached")]
    frees = [r["free_gb"] for r in results if r["free_gb"] is not None]
    min_free = min(frees) if frees else None

    for r in results:
        if r["status"] not in ("ok", "cached", "tag_not_found"):
            module.warn("Prefetch of OCP %s failed (%s); later tasks will pull it themselves"
                        % (r["ocp_version"], r["status"]))

    msg = "Prefetched %d/%d OCP indexes in %.0fs with %d workers" % (
        len(cached), len(tags), elapsed, workers)
    if min_free is not None:
        msg += " (lowest free disk %.1f GB)" % min_free
    return {
        "changed": False,
        "msg": msg,
        "results": results,
        "not_cached": not_cached,
        "min_free_gb": min_free,
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            ocp_versions=dict(type="list", elements="str", required=True),
            index_cache_dir=dict(type="path", required=True),
            package_name=dict(type="str", default="ansible-automation-platform-operator"),
            registry=dict(type="str", default="registry.redhat.io"),
            workers=dict(type="int", default=3),
        ),
        supports_check_mode=False,
    )
    module.exit_json(**run(module))


if __name__ == "__main__":
    main()
