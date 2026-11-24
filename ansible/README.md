# Ansible AAP CSV Update

This directory contains an Ansible playbook and roles to maintain the AAP operator CSV compatibility matrix.

## Requirements

- Ansible 2.9+
- Podman
- Access to `registry.redhat.io` (Red Hat registry)

## Playbook flow

The playbook runs three roles in order:

| Role | Purpose |
|------|---------|
| `aap_matrix_common` | Podman check, registry login, shared variables, **discovery** of OCP versions and AAP channels from the public Red Hat catalog API, and a parallel **prefetch** of every index into the run cache |
| `aap_csv_update` | For each AAP stream, read its channels from the newest published OCP index and add missing operator CSV rows (creating `data/AAP_<ver>.csv` for a new stream) |
| `aap_openshift_support` | Scrape all discovered OCP index tags, update **OpenShift Support** ranges on every `data/AAP_*.csv` |

### Discovery

Each run queries `https://catalog.redhat.com/api/containers/v1/operators/bundles` (public, no credentials) for every bundle of `ansible-automation-platform-operator`. From that it derives:

- **OCP versions** to scan: every OCP version the catalog lists for the package, from `aap_matrix_min_ocp_version` up.
- **AAP streams**: every `stable-X.Y` / `stable-X.Y-cluster-scoped` channel pair from `aap_matrix_min_aap_version` up, each with the newest OCP tags that carry it (tried newest first; a tag listed in the catalog whose index image isn't published yet is skipped).

The operator index images remain the source of truth for the data; the catalog only decides which tags and channels to read. Only the operator package's catalog (`/configs/<package>`) is copied out of each index, falling back to all of `/configs` if the package has no directory of its own. Extracts are cached under `.cache/operator-index/` for the duration of a run so an index read by `aap_csv_update` isn't pulled again by the OpenShift Support scrape.

OpenShift Support merge rules:

- **Blank cell** — filled from min/max OCP index tags where the bundle appears.
- **Existing range** — lowest OCP is preserved (EOL floor); highest OCP is extended when a newer index tag lists the bundle.

## Setup

### Registry authentication

Shared variables live in **`roles/aap_matrix_common/defaults/main.yml`**.

**Option A: Environment variables**
```bash
export REGISTRY_USERNAME=YOUR_REDHAT_USER
export REGISTRY_PASSWORD=YOUR_REDHAT_PASSWORD
ansible-playbook playbook.yml
```

**Option B: Override at run time**
```bash
ansible-playbook playbook.yml \
  -e "aap_matrix_registry_username=USER" \
  -e "aap_matrix_registry_password=PASS"
```

**Option C: Login beforehand**
```bash
podman login registry.redhat.io
ansible-playbook playbook.yml
```

### Run the playbook

```bash
cd ansible
ansible-playbook playbook.yml
```

## GitHub Actions

Workflow **Update AAP CSV matrix** (`.github/workflows/update-csv-matrix.yml`) runs the playbook, regenerates the markdown tables, README version list and `OCP_INDEX_COVERAGE.md`, and commits everything in one commit.

Required secrets: `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`.

## Shared variables (`aap_matrix_*`)

Edit **`roles/aap_matrix_common/defaults/main.yml`**:

| Variable | Description |
|----------|-------------|
| `aap_matrix_data_dir` | Directory with `AAP_*.csv` files |
| `aap_matrix_overlaps_json` | Operator index scrape output (default `scripts/csv-overlaps.json`) |
| `aap_matrix_min_ocp_version` | Oldest OCP version to scan (default `4.11`) |
| `aap_matrix_min_aap_version` | Oldest AAP stream to track (default `2.4`) |
| `aap_matrix_max_ocp_candidates` | Newest OCP tags tried per AAP stream (default `3`) |
| `aap_matrix_prefetch_workers` | Operator indexes pulled at once into the run cache before the loops (default `3`, `0` = off) |
| `aap_matrix_create_missing_csv` | Create `data/AAP_<ver>.csv` for a newly discovered AAP stream (default `true`) |
| `aap_matrix_operator_index_ocp_versions` | Manual override of the OCP tags to scan (empty = discover) |
| `aap_matrix_aap_streams` | Manual override of the AAP streams (empty = discover); see the commented example in defaults |
| `aap_matrix_columns` | CSV column indices |

## Modules

### `aap_operator_index_prefetch` (role `aap_matrix_common`)

Pulls every discovered OCP index tag, `aap_matrix_prefetch_workers` at a time, and extracts the package's catalog into the run cache. The later per-stream and per-OCP loops then read each index from the cache. It is only an accelerator: a tag that fails here is pulled by those loops exactly as without it, and extracts are moved into place only once complete.

### `aap_csv_update` (role `aap_csv_update`)

One Ansible loop iteration per AAP stream. Reads the stream's channels from the first published index in its `ocp_versions` list and pairs cluster/namespace builds into rows. If `data/AAP_<ver>.csv` doesn't exist (new stream) it is created with the header of the newest existing CSV, once the index confirms the channel has builds.

### `aap_operator_index_scrape` + `aap_operator_index_overlaps_write` (role `aap_openshift_support`)

One Ansible loop iteration per OCP index tag (live task output per pull), then assembles `csv-overlaps.json`.

### `aap_openshift_support_update` (role `aap_openshift_support`)

Updates OpenShift Support from the overlaps JSON.

## Coverage report

The scrape output (`scripts/csv-overlaps.json`, git-ignored) is rendered into [`OCP_INDEX_COVERAGE.md`](../OCP_INDEX_COVERAGE.md) by `scripts/csv-overlaps-to-markdown.py`. The scheduled workflow does this on every run; to do it locally after a playbook run:

```bash
python3 scripts/csv-overlaps-to-markdown.py
```
