# Ansible AAP CSV Update

This directory contains an Ansible playbook and role to update the AAP operator CSV compatibility matrix.

## Requirements

- Ansible 2.9+
- Podman
- Access to `registry.redhat.io` (Red Hat registry)

## Setup

### 1. Registry Authentication

The role needs to pull images from the Red Hat registry. Registry URL and credentials are defined in **`roles/aap_csv_update/defaults/main.yml`**, which uses environment variables by default.

**Option A: Set environment variables**
```bash
export REGISTRY_USERNAME=YOUR_REDHAT_USER
export REGISTRY_PASSWORD=YOUR_REDHAT_PASSWORD
ansible-playbook playbook.yml
```

**Option B: Override at run time**
```bash
ansible-playbook playbook.yml -e "aap_csv_update_registry_username=USER" -e "aap_csv_update_registry_password=PASS"
```

**Option C: Login beforehand**
```bash
podman login registry.redhat.io
# Enter your Red Hat username and password when prompted
```
Then run the playbook without credentials - it will verify you're already logged in.

### 2. Run the Playbook

From the `ansible` directory:
```bash
cd ansible
ansible-playbook playbook.yml
```

Or from the project root:
```bash
ansible-playbook ansible/playbook.yml -i ansible/inventory.yml
```

### 3. GitHub Actions (on-demand)

The repo includes a workflow **Update AAP CSV matrix** (`.github/workflows/update-csv-matrix.yml`) that you can run manually:

1. **Actions** → **Update AAP CSV matrix** → **Run workflow**
2. Configure these **repository secrets** (Settings → Secrets and variables → Actions):
   - `REGISTRY_USERNAME`: Red Hat registry username
   - `REGISTRY_PASSWORD`: Red Hat registry password (e.g. token)
3. The workflow installs Podman and Ansible, runs the playbook, then commits and pushes any changes under `data/*.csv`.

## Role: aap_csv_update

The playbook applies the **`aap_csv_update`** role (`roles/aap_csv_update/`).

### Variables

Defaults live in **`roles/aap_csv_update/defaults/main.yml`**. Override at run time with `-e` or in your own vars file.

| Variable | Default | Description |
|----------|---------|-------------|
| `aap_csv_update_registry` | `registry.redhat.io` | Container registry |
| `aap_csv_update_registry_username` | env `REGISTRY_USERNAME` | Registry username |
| `aap_csv_update_registry_password` | env `REGISTRY_PASSWORD` | Registry password |
| `aap_csv_update_data_dir` | `{{ role_path }}/../../data` | Directory containing AAP_*.csv files |
| `aap_csv_update_columns` | see defaults | Column indices for CSV fields |
| `aap_csv_update_loops` | see defaults | OCP versions, channels, and AAP versions to check |

Edit **`defaults/main.yml`** to change OCP versions, channels, and AAP versions checked.

## Module: aap_csv_update

The `aap_csv_update` module (in `roles/aap_csv_update/library/`):
- Uses `columns` and `loops` (from role defaults) or a config file for OCP versions and channels
- Pulls operator index images via podman
- Extracts CSV versions from namespace and cluster-scoped channels
- Pairs versions by timestamp
- Updates CSV files with missing entries

### Module Parameters

- `columns`: (optional) Dict of column indices; use with `loops` from vars
- `loops`: (optional) List of loop entries; when set, `config_file` is ignored
- `config_file`: (optional) Path to check-versions.json when not using vars
- `data_dir`: Directory containing AAP_*.csv files
- `registry`: Registry URL (for future use)

### Returns

- `changed`: Whether any files were modified
- `messages`: List of status messages
- `rows_added`: Number of new rows added
- `rows_updated`: Number of existing rows updated
