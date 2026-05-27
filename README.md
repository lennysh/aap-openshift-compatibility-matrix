# Red Hat Ansible Automation Platform - OpenShift Compatibility Matrix

[![GitHub last commit](https://img.shields.io/github/last-commit/lennysh/aap-openshift-compatibility-matrix.svg)](https://github.com/lennysh/aap-openshift-compatibility-matrix/commits/main) [![GitHub license](https://img.shields.io/github/license/lennysh/aap-openshift-compatibility-matrix.svg)](https://github.com/lennysh/aap-openshift-compatibility-matrix/blob/main/LICENSE) [![Contributions welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](https://github.com/lennysh/aap-openshift-compatibility-matrix/pulls) ![GitHub contributors](https://img.shields.io/github/contributors/lennysh/aap-openshift-compatibility-matrix) ![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/lennysh/aap-openshift-compatibility-matrix/update-compatibility-md-tables.yml) ![GitHub Issues or Pull Requests](https://img.shields.io/github/issues/lennysh/aap-openshift-compatibility-matrix)

This repository provides a centralized, community-driven tracker for Red Hat Ansible Automation Platform (AAP) operator and component versions, specifically for deployments on OpenShift. It aims to offer a clear and quick reference for mapping AAP operator releases to their corresponding component versions, such as Controller, EDA, Hub, and more.

## 📋 Compatibility Tables

For easy viewing, the raw data has been converted into user-friendly Markdown tables, which are generated from their respective CSV files.

* [**Red Hat Ansible Automation Platform 2.4**](./AAP_24.md)
* [**Red Hat Ansible Automation Platform 2.5**](./AAP_25.md)
* [**Red Hat Ansible Automation Platform 2.6**](./AAP_26.md)
* [**Red Hat Ansible Automation Platform 2.7**](./AAP_27.md)

## ⚙️ How It Works

The core of this repository is a simple, automated workflow designed for clarity and easy maintenance.

1.  **Raw Data**: All version information is maintained in respective `AAP_2x.csv` files. These are the single source of truth for the entire repository and the only files that should be edited manually.

2.  **Conversion Script**: A bash script (`csv2md.sh`) reads the `AAP_2x.csv` files. This script can filter rows, remove columns, and format URLs into clickable links.

3.  **Markdown Output**: The script processes the data from each `AAP_2x.csv` file and generates the two Markdown files (`AAP_24.md` and `AAP_25.md`), creating clean, readable tables.

### Automation Example

The Markdown files are automatically generated using the `csv2md.sh` script included in this repository. For example, to regenerate the AAP 2.5 table, the following command is used:

```bash
./scripts/csv2md.sh \
  -t "Red Hat Ansible Automation Platform 2.5 - OpenShift Operator Component versions" \
  ./data/AAP_25.csv > AAP_25.md
```

## 🤝 Contributing

Found a mistake or have an update for a new release? Contributions are highly encouraged!

To contribute, please **submit a pull request with your changes to the `AAP_2x.csv` files only**. Do not edit the Markdown files directly, as they are overwritten by the automation script. Once your pull request is merged, the script will be re-run to update the tables.

## ✨ Contributors

A big thank you to all the contributors who have helped improve this project! You can see a full list of everyone who has contributed on the [contributors page](https://github.com/lennysh/aap-openshift-compatibility-matrix/graphs/contributors).

<a href = "https://github.com/lennysh/aap-openshift-compatibility-matrix/graphs/contributors">
  <img src = "https://contrib.rocks/image?repo=lennysh/aap-openshift-compatibility-matrix"/>
</a>

## ✍️ Authors

* [CastawayEGR](https://github.com/CastawayEGR) *(Deserves most of the credit!)*
* [LennySh](https://github.com/lennysh)

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.