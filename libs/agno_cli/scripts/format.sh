#!/bin/bash

############################################################################
# Format the agno_cli library using ruff
# Usage: ./libs/agno_cli/scripts/format.sh
############################################################################

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGNO_CLI_DIR="$(dirname ${CURR_DIR})"
source ${CURR_DIR}/_utils.sh

print_heading "Formatting agno_cli"

print_heading "Running: ruff format ${AGNO_CLI_DIR}"
ruff format ${AGNO_CLI_DIR}

print_heading "Running: ruff check --select I --fix ${AGNO_CLI_DIR}"
ruff check --select I --fix ${AGNO_CLI_DIR}
