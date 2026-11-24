#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: aap_matrix_discover
short_description: Discover OCP versions and AAP channels for the AAP operator from the Red Hat catalog API
description:
  - Queries the public Red Hat container catalog (Pyxis) for every bundle of the operator package.
  - Returns the OCP versions whose operator index carries the package, and one entry per AAP
    stream (stable-X.Y + stable-X.Y-cluster-scoped channel pair).
  - No registry credentials needed. The operator index images remain the source of truth for the
    actual data; this only decides which index tags and channels to look at.
version_added: "1.1.0"
options:
  package_name:
    description: OLM package name.
    type: str
    default: ansible-automation-platform-operator
  catalog_api_url:
    description: Pyxis operator bundles endpoint.
    type: str
    default: https://catalog.redhat.com/api/containers/v1/operators/bundles
  min_ocp_version:
    description: Ignore OCP versions older than this.
    type: str
    default: "4.11"
  min_aap_version:
    description: Ignore AAP streams older than this.
    type: str
    default: "2.4"
  timeout:
    description: HTTP timeout in seconds per request.
    type: int
    default: 60
author:
  - lennysh
'''

RETURN = r'''
ocp_versions:
  description: OCP versions (ascending) whose catalog carries the package, >= min_ocp_version.
  type: list
  elements: str
aap_streams:
  description: One entry per AAP stream, ascending.
  type: list
  elements: dict
  contains:
    aap_version: {description: "e.g. 2.7", type: str}
    namespace_channel: {description: "e.g. stable-2.7", type: str}
    cluster_channel: {description: "e.g. stable-2.7-cluster-scoped (empty if none)", type: str}
bundle_count:
  description: Number of catalog bundle records read.
  type: int
bundle_first_seen:
  description:
    - Earliest catalog record time (ISO 8601, UTC) per operator CSV name. Used as the
      Release Date of newly added rows, so it doesn't depend on when the run happens.
  type: dict
'''

import json
import re
import time
from datetime import datetime, timezone

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import urlencode
from ansible.module_utils.urls import open_url

_CHANNEL_RE = re.compile(r"^stable-(\d+\.\d+)(-cluster-scoped)?$")
_PAGE_SIZE = 500
_MAX_PAGES = 200
_ATTEMPTS = 3


def vkey(v):
    return tuple(int(p) for p in str(v).split(".") if p.isdigit())


def _get_json(module, url):
    """GET url as JSON, retrying transient failures (response times vary 4-35 s)."""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            resp = open_url(url, timeout=module.params["timeout"], headers={"Accept": "application/json"})
            return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001 - surface any transport/parse error clearly
            if attempt == _ATTEMPTS:
                module.fail_json(msg="Red Hat catalog API request failed: %s" % exc, url=url)
            module.warn("Red Hat catalog API request failed (attempt %d), retrying: %s" % (attempt, exc))
            time.sleep(5 * attempt)


def fetch_bundles(module):
    p = module.params
    records = {}
    for page in range(_MAX_PAGES):
        query = urlencode({
            "filter": "package==%s" % p["package_name"],
            # Without an explicit sort the API pages inconsistently: one run returned
            # 2,035 records but only 2,014 distinct ones (21 duplicated, 21 never seen)
            "sort_by": "_id[asc]",
            "page_size": _PAGE_SIZE,
            "page": page,
            "include": "data._id,data.csv_name,data.ocp_version,data.channel_name,data.creation_date,total",
        })
        url = "%s?%s" % (p["catalog_api_url"], query)
        payload = _get_json(module, url)
        data = payload.get("data") or []
        for rec in data:
            records[rec.get("_id") or len(records)] = rec
        total = payload.get("total")
        if len(data) < _PAGE_SIZE or (isinstance(total, int) and len(records) >= total):
            break
    return list(records.values())


def first_seen_by_csv(records):
    """Earliest creation_date per csv_name, as ISO 8601 UTC strings."""
    first = {}
    for rec in records:
        name = str(rec.get("csv_name") or "").strip()
        raw = str(rec.get("creation_date") or "").strip()
        if not name or not raw:
            continue
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)
        if name not in first or when < first[name]:
            first[name] = when
    return {name: when.isoformat() for name, when in first.items()}


def run(module):
    p = module.params
    min_ocp = vkey(p["min_ocp_version"])
    min_aap = vkey(p["min_aap_version"])

    records = fetch_bundles(module)
    if not records:
        module.fail_json(
            msg="Red Hat catalog API returned no bundles for package %s" % p["package_name"]
        )

    ocp_versions = set()
    channels = set()
    for rec in records:
        ocp = str(rec.get("ocp_version") or "").strip()
        channel = str(rec.get("channel_name") or "").strip()
        if not re.match(r"^\d+\.\d+$", ocp) or vkey(ocp) < min_ocp:
            continue
        ocp_versions.add(ocp)
        if channel:
            channels.add(channel)

    streams = {}
    for channel in channels:
        m = _CHANNEL_RE.match(channel)
        if not m or vkey(m.group(1)) < min_aap:
            continue
        s = streams.setdefault(m.group(1), {"namespace": None, "cluster": None})
        s["cluster" if m.group(2) else "namespace"] = channel

    aap_streams = []
    for aap in sorted(streams, key=vkey):
        s = streams[aap]
        if not s["namespace"]:
            module.warn("AAP %s has only a cluster-scoped channel in the catalog; skipping" % aap)
            continue
        aap_streams.append({
            "aap_version": aap,
            "namespace_channel": s["namespace"],
            "cluster_channel": s["cluster"] or "",
        })

    if not ocp_versions or not aap_streams:
        module.fail_json(
            msg="Catalog discovery found no usable OCP versions or AAP channels",
            ocp_versions=sorted(ocp_versions, key=vkey),
            aap_streams=aap_streams,
        )

    return {
        "changed": False,
        "ocp_versions": sorted(ocp_versions, key=vkey),
        "aap_streams": aap_streams,
        "bundle_count": len(records),
        "bundle_first_seen": first_seen_by_csv(records),
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            package_name=dict(type="str", default="ansible-automation-platform-operator"),
            catalog_api_url=dict(
                type="str",
                default="https://catalog.redhat.com/api/containers/v1/operators/bundles",
            ),
            min_ocp_version=dict(type="str", default="4.11"),
            min_aap_version=dict(type="str", default="2.4"),
            timeout=dict(type="int", default=60),
        ),
        supports_check_mode=True,
    )
    module.exit_json(**run(module))


if __name__ == "__main__":
    main()
