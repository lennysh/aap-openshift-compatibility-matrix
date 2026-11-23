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
    stream (stable-X.Y + stable-X.Y-cluster-scoped channel pair) with the OCP index tags to read it from.
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
  max_ocp_candidates:
    description: How many of the newest OCP tags to offer per AAP stream (tried newest first).
    type: int
    default: 3
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
    ocp_versions: {description: OCP tags carrying the channel, newest first, type: list}
bundle_count:
  description: Number of catalog bundle records read.
  type: int
'''

import json
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import urlencode
from ansible.module_utils.urls import open_url

_CHANNEL_RE = re.compile(r"^stable-(\d+\.\d+)(-cluster-scoped)?$")
_PAGE_SIZE = 100
_MAX_PAGES = 200


def vkey(v):
    return tuple(int(p) for p in str(v).split(".") if p.isdigit())


def fetch_bundles(module):
    p = module.params
    records = []
    for page in range(_MAX_PAGES):
        query = urlencode({
            "filter": "package==%s" % p["package_name"],
            "page_size": _PAGE_SIZE,
            "page": page,
            "include": "data.csv_name,data.ocp_version,data.channel_name,total",
        })
        url = "%s?%s" % (p["catalog_api_url"], query)
        try:
            resp = open_url(url, timeout=p["timeout"], headers={"Accept": "application/json"})
            payload = json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001 - surface any transport/parse error clearly
            module.fail_json(msg="Red Hat catalog API request failed: %s" % exc, url=url)
        data = payload.get("data") or []
        records.extend(data)
        if len(data) < _PAGE_SIZE:
            break
    return records


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
    channel_ocps = {}  # channel -> set(ocp)
    for rec in records:
        ocp = str(rec.get("ocp_version") or "").strip()
        channel = str(rec.get("channel_name") or "").strip()
        if not re.match(r"^\d+\.\d+$", ocp) or vkey(ocp) < min_ocp:
            continue
        ocp_versions.add(ocp)
        if channel:
            channel_ocps.setdefault(channel, set()).add(ocp)

    streams = {}
    for channel, ocps in channel_ocps.items():
        m = _CHANNEL_RE.match(channel)
        if not m or vkey(m.group(1)) < min_aap:
            continue
        s = streams.setdefault(m.group(1), {"namespace": None, "cluster": None, "ocps": set()})
        s["cluster" if m.group(2) else "namespace"] = channel
        s["ocps"] |= ocps

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
            "ocp_versions": sorted(s["ocps"], key=vkey, reverse=True)[: p["max_ocp_candidates"]],
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
            max_ocp_candidates=dict(type="int", default=3),
            timeout=dict(type="int", default=60),
        ),
        supports_check_mode=True,
    )
    module.exit_json(**run(module))


if __name__ == "__main__":
    main()
