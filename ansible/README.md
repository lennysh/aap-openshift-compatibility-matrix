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
| `aap_matrix_common` | Podman check, registry login, shared variables |
| `aap_csv_update` | Add missing operator CSV rows from configured OCP channels |
| `aap_openshift_support` | Scrape all OCP index tags, update **OpenShift Support** ranges on every `data/AAP_*.csv` |

OpenShift Support merge rules (same as `scripts/merge-openshift-support-from-overlaps.py`):

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

Workflow **Update AAP CSV matrix** (`.github/workflows/update-csv-matrix.yml`) runs the playbook, then commits `data/*.csv` and regenerated markdown.

Required secrets: `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`.

## Shared variables (`aap_matrix_*`)

Edit **`roles/aap_matrix_common/defaults/main.yml`**:

| Variable | Description |
|----------|-------------|
| `aap_matrix_data_dir` | Directory with `AAP_*.csv` files |
| `aap_matrix_overlaps_json` | Operator index scrape output (default `scripts/csv-overlaps.json`) |
| `aap_matrix_operator_index_ocp_versions` | All OCP tags to scan for OpenShift Support |
| `aap_matrix_loops` | OCP/channel/AAP entries for CSV row updates |
| `aap_matrix_columns` | CSV column indices |

## Modules

### `aap_csv_update` (role `aap_csv_update`)

Adds missing operator CSV version rows using configured channels.

### `aap_operator_index_scrape` + `aap_operator_index_overlaps_write` (role `aap_openshift_support`)

One Ansible loop iteration per OCP index tag (live task output per pull), then assembles `csv-overlaps.json` (replaces `scripts/find-csv-overlaps.sh` in automation).

### `aap_openshift_support_update` (role `aap_openshift_support`)

Updates OpenShift Support from the overlaps JSON (replaces `scripts/merge-openshift-support-from-overlaps.py` in automation).

## Legacy scripts

`scripts/find-csv-overlaps.sh` and `scripts/merge-openshift-support-from-overlaps.py` remain available for manual runs; the playbook uses the Ansible modules above.
