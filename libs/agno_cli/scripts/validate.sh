#!/bin/bash

############################################################################
# Validate the agno_cli library using ruff and mypy
# Usage: ./libs/agno_cli/scripts/validate.sh
############################################################################

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGNO_CLI_DIR="$(dirname ${CURR_DIR})"
source ${CURR_DIR}/_utils.sh

print_heading "Validating agno_cli"

print_heading "Running: ruff check ${AGNO_CLI_DIR}"
ruff check ${AGNO_CLI_DIR}

print_heading "Running: mypy ${AGNO_CLI_DIR}/agno_cli --config-file ${AGNO_CLI_DIR}/pyproject.toml"
mypy ${AGNO_CLI_DIR}/agno_cli --config-file ${AGNO_CLI_DIR}/pyproject.toml
