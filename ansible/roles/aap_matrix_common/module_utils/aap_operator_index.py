# -*- coding: utf-8 -*-
# Shared helpers for pulling and parsing Red Hat operator index images.

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import os
import re
import shutil
import subprocess
import tempfile


def version_key(version):
    parts = version.strip().split(".")
    return tuple(int(p) for p in parts if p.isdigit())


def run_command(module, cmd, check_rc=True):
    result = subprocess.run(
        cmd,
        capture_output=True,
        shell=isinstance(cmd, str),
        text=True,
    )
    if check_rc and result.returncode != 0:
        module.fail_json(
            msg="Command failed: %s" % cmd,
            stderr=result.stderr,
            stdout=result.stdout,
        )
    return result.returncode, result.stdout, result.stderr


def extract_operator_index(module, ocp_version, registry="registry.redhat.io"):
    """Pull operator index image and extract /configs. Returns temp dir or None."""
    image = "%s/redhat/redhat-operator-index:v%s" % (registry, ocp_version)
    tmp_dir = tempfile.mkdtemp()

    rc, _, err = run_command(module, ["podman", "pull", image], check_rc=False)
    if rc != 0:
        module.warn("Failed to pull image %s: %s" % (image, err))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None, "pull_failed"

    result = subprocess.run(["podman", "create", image], capture_output=True, text=True)
    if result.returncode != 0:
        module.warn("Failed to create container for %s" % image)
        subprocess.run(["podman", "rmi", image], capture_output=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None, "create_failed"

    container_id = result.stdout.strip()
    cp_result = subprocess.run(
        "podman cp %s:/configs - | tar -x -C %s" % (container_id, tmp_dir),
        shell=True,
        capture_output=True,
    )
    subprocess.run(["podman", "rm", "-v", container_id], capture_output=True)

    if cp_result.returncode != 0:
        module.warn("Failed to extract configs for OCP %s" % ocp_version)
        subprocess.run(["podman", "rmi", image], capture_output=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None, "tar_extract_failed"

    subprocess.run(["podman", "rmi", image], capture_output=True)
    return tmp_dir, "ok"


def iter_olm_channel_objects(content):
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(content):
        obj, end = decoder.raw_decode(content[idx:])
        idx += end
        yield obj
        while idx < len(content) and content[idx] in " \t\n\r":
            idx += 1


def extract_csv_versions_from_dir(tmp_dir, package_name, channel=None):
    """Collect operator CSV names from index configs.

    If channel is set, only that channel; otherwise all channels for package.
    Returns (sorted_versions, config_json_files, merged_channel_keys).
    """
    by_channel = {}
    config_json_files = 0
    channel_objects = 0

    for root, _, files in os.walk(tmp_dir):
        for fname in files:
            if not fname.endswith(".json"):
                continue
            config_json_files += 1
            path = os.path.join(root, fname)
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    content = fp.read()
                for obj in iter_olm_channel_objects(content):
                    if obj.get("schema") != "olm.channel":
                        continue
                    if obj.get("package") != package_name:
                        continue
                    ch_name = obj.get("name", "")
                    if channel is not None and ch_name != channel:
                        continue
                    channel_objects += 1
                    versions = by_channel.setdefault(ch_name, set())
                    for entry in obj.get("entries", []):
                        name = entry.get("name", entry) if isinstance(entry, dict) else entry
                        if (
                            name
                            and isinstance(name, str)
                            and name.startswith("aap-operator.")
                        ):
                            versions.add(name)
            except (json.JSONDecodeError, KeyError, ValueError, OSError):
                continue

    if channel is not None:
        versions = by_channel.get(channel, set())
        return (
            sorted(versions, key=version_key_from_csv),
            config_json_files,
            1 if channel in by_channel else 0,
        )

    all_versions = set()
    for versions in by_channel.values():
        all_versions |= versions
    return (
        sorted(all_versions, key=version_key_from_csv),
        config_json_files,
        len(by_channel),
    )


def version_key_from_csv(csv_name):
    match = re.search(r"-0\.(\d+)$", csv_name)
    return int(match.group(1)) if match else 0


def scrape_all_csv_versions(module, ocp_version, package_name, registry="registry.redhat.io"):
    """Scrape one OCP index tag; return status dict and sorted CSV name list."""
    tmp_dir, status = extract_operator_index(module, ocp_version, registry=registry)
    if tmp_dir is None:
        return {
            "status": status,
            "config_json_files": 0,
            "olm_channel_objects_merged": 0,
            "unique_operator_csv_count": 0,
            "csv_versions": [],
        }

    try:
        versions, config_json_files, channel_count = extract_csv_versions_from_dir(
            tmp_dir, package_name, channel=None
        )

        if status == "ok":
            if config_json_files == 0:
                scrape_status = "ok_no_config_json_files"
            elif not versions:
                scrape_status = "ok_no_operator_csv_in_channels"
            else:
                scrape_status = "ok"
        else:
            scrape_status = status

        return {
            "status": scrape_status,
            "config_json_files": config_json_files,
            "olm_channel_objects_merged": channel_count,
            "unique_operator_csv_count": len(versions),
            "csv_versions": versions,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
