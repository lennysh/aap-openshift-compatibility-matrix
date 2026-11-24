#!/usr/bin/env python3
"""
Fingerprint the Red Hat catalog API's listing of AAP operator bundles.

The scheduled "Update AAP CSV matrix" workflow runs this every hour and only runs the
full operator-index scrape (~15 index pulls) when the fingerprint differs from its last
full run of the day. It costs a few HTTP requests and needs no registry credentials.

The fingerprint covers every (operator CSV, OCP version, channel) record the API lists
for the package, so a new build, a build listed for another OCP version, or a new
channel changes it. The API's per-OCP listing is incomplete, which is why the workflow
also runs the full scrape once a day regardless.

Prints `fingerprint=<sha256 hex>` (GITHUB_OUTPUT format) and exits 0, or exits 2 if the
API can't be read or returns no records.

Usage: python3 scripts/catalog-fingerprint.py [--package NAME] [--api URL]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request

# Keep in sync with aap_matrix_catalog_api_url / aap_matrix_operator_package in
# ansible/roles/aap_matrix_common/defaults/main.yml
DEFAULT_API = "https://catalog.redhat.com/api/containers/v1/operators/bundles"
DEFAULT_PACKAGE = "ansible-automation-platform-operator"
PAGE_SIZE = 500
MAX_PAGES = 200
ATTEMPTS = 3


def get_json(url: str, timeout: int) -> dict:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "aap-compatibility-matrix"}
    )
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - retry any transport/parse error
            if attempt == ATTEMPTS:
                raise
            print(f"catalog API request failed (attempt {attempt}), retrying: {exc}", file=sys.stderr)
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def fetch_records(api: str, package: str, timeout: int) -> list[dict]:
    records: dict = {}
    for page in range(MAX_PAGES):
        query = urllib.parse.urlencode({
            "filter": f"package=={package}",
            "sort_by": "_id[asc]",  # paging without a sort repeats and skips records
            "page_size": PAGE_SIZE,
            "page": page,
            "include": "data._id,data.csv_name,data.ocp_version,data.channel_name,total",
        })
        payload = get_json(f"{api}?{query}", timeout)
        data = payload.get("data") or []
        for rec in data:
            records[rec.get("_id") or len(records)] = rec
        total = payload.get("total")
        if len(data) < PAGE_SIZE or (isinstance(total, int) and len(records) >= total):
            break
    return list(records.values())


def fingerprint(records: list[dict]) -> tuple[str, int]:
    keys = sorted({
        (str(r.get("csv_name") or ""), str(r.get("ocp_version") or ""), str(r.get("channel_name") or ""))
        for r in records
    })
    return hashlib.sha256(json.dumps(keys).encode("utf-8")).hexdigest(), len(keys)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--package", default=DEFAULT_PACKAGE)
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    try:
        records = fetch_records(args.api, args.package, args.timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: catalog API unavailable: {exc}", file=sys.stderr)
        return 2
    if not records:
        print(f"Error: catalog API returned no records for {args.package}", file=sys.stderr)
        return 2

    digest, count = fingerprint(records)
    print(f"{count} catalog records for {args.package}", file=sys.stderr)
    print(f"fingerprint={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
