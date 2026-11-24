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


def _warn_if_no_json(module, pkg_dir, ocp_version):
    """The parsers only read .json; say so loudly if a catalog is YAML-only."""
    names = [f for _, _, files in os.walk(pkg_dir) for f in files]
    if names and not any(f.endswith(".json") for f in names):
        module.warn(
            "OCP %s: %s has no .json catalog files (found: %s); only JSON is parsed"
            % (ocp_version, pkg_dir, ", ".join(sorted(names)[:5]))
        )


def copy_index_configs(module, container_id, ocp_version, dest_dir, package_name=None):
    """Copy the index's file-based catalog from a created container into dest_dir.

    With package_name, copies only /configs/<package_name> (the AAP catalog is ~85 MB
    of a ~230 MB /configs in v4.18), falling back to the whole /configs if the package
    has no directory of its own. Returns True on success.
    """
    if package_name:
        result = subprocess.run(
            ["podman", "cp", "%s:/configs/%s" % (container_id, package_name), dest_dir],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            _warn_if_no_json(module, os.path.join(dest_dir, package_name), ocp_version)
            return True
        if not re.search(r"could not be found|no such file", result.stderr or "", re.IGNORECASE):
            module.warn(
                "Failed to copy /configs/%s for OCP %s: %s"
                % (package_name, ocp_version, (result.stderr or "").strip())
            )
            return False
        # No per-package directory: fall through and copy everything
    cp_result = subprocess.run(
        "podman cp %s:/configs - | tar -x -C %s" % (container_id, dest_dir),
        shell=True,
        capture_output=True,
    )
    return cp_result.returncode == 0


def extract_operator_index(module, ocp_version, registry="registry.redhat.io", package_name=None):
    """Pull operator index image and extract its catalog. Returns (temp dir or None, status).

    Pass package_name to copy only that package's catalog (see copy_index_configs).
    """
    image = "%s/redhat/redhat-operator-index:v%s" % (registry, ocp_version)
    tmp_dir = tempfile.mkdtemp()

    rc, _, err = run_command(module, ["podman", "pull", image], check_rc=False)
    if rc != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # A tag that isn't published yet (e.g. a future OCP release listed ahead of GA)
        # is expected, not a scrape failure.
        if re.search(r"manifest unknown|not found", err or "", re.IGNORECASE):
            module.warn("Operator index %s not published yet; skipping" % image)
            return None, "tag_not_found"
        module.warn("Failed to pull image %s: %s" % (image, err))
        return None, "pull_failed"

    result = subprocess.run(["podman", "create", image], capture_output=True, text=True)
    if result.returncode != 0:
        module.warn("Failed to create container for %s" % image)
        subprocess.run(["podman", "rmi", image], capture_output=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None, "create_failed"

    container_id = result.stdout.strip()
    copied = copy_index_configs(module, container_id, ocp_version, tmp_dir, package_name)
    subprocess.run(["podman", "rm", "-v", container_id], capture_output=True)

    if not copied:
        module.warn("Failed to extract configs for OCP %s" % ocp_version)
        subprocess.run(["podman", "rmi", image], capture_output=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None, "tar_extract_failed"

    subprocess.run(["podman", "rmi", image], capture_output=True)
    return tmp_dir, "ok"


def iter_olm_channel_objects(content):
    """Yield each top-level JSON value of a file-based catalog (concatenated objects).

    Decodes in place via raw_decode's start index: slicing off the remaining text for
    every object copies the file once per object, which on an ~85 MB AAP catalog made
    parsing ~14x slower.
    """
    decoder = json.JSONDecoder()
    idx = 0
    end = len(content)
    while True:
        while idx < end and content[idx] in " \t\n\r":
            idx += 1
        if idx >= end:
            return
        obj, idx = decoder.raw_decode(content, idx)
        yield obj


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
                with open(path, "r", encoding="utf-8-sig") as fp:
                    content = fp.read()
                for obj in iter_olm_channel_objects(content):
                    if not isinstance(obj, dict) or obj.get("schema") != "olm.channel":
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


def cached_index_dir(cache_dir, ocp_version):
    """Return the cached extract for ocp_version under cache_dir if it has configs, else None."""
    if not cache_dir:
        return None
    sub = os.path.join(os.path.abspath(os.path.expanduser(cache_dir)), "ocp-%s" % ocp_version)
    if os.path.isdir(sub):
        for _, _, files in os.walk(sub):
            if any(f.endswith(".json") for f in files):
                return sub
    return None


def scrape_all_csv_versions(
    module, ocp_version, package_name, registry="registry.redhat.io", cache_dir=None
):
    """Scrape one OCP index tag; return status dict and sorted CSV name list.

    Reuses an extract cached earlier in the run (by aap_csv_update) instead of pulling again.
    """
    cached = cached_index_dir(cache_dir, ocp_version)
    if cached:
        tmp_dir, status = cached, "ok"
    else:
        tmp_dir, status = extract_operator_index(
            module, ocp_version, registry=registry, package_name=package_name
        )
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
        if not cached:
            shutil.rmtree(tmp_dir, ignore_errors=True)
