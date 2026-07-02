"""Agno cli

This is the entrypoint for the `agno` cli application.
"""

from typing import Optional

import typer

from agno.cli.infra_cli import infra_cli as infra_subcommands
from agno.utilities.logging import set_log_level_to_debug

agno_cli = typer.Typer(
    help="""\b
Agno is a lightweight framework for building Agent Systems.
\b
Usage:
1. Run `ag setup` to create and start a new AgentOS project in one command
2. Run `ag infra create` to create a new Agentics Infrastructure project from a template
3. Run `ag infra up` to start the infrastructure
4. Run `ag infra down` to stop the infrastructure
""",
    no_args_is_help=True,
    add_completion=False,
    invoke_without_command=True,
    options_metavar="\b",
    subcommand_metavar="[COMMAND] [OPTIONS]",
    pretty_exceptions_show_locals=False,
)


@agno_cli.command(short_help="Set up a new AgentOS: install packages, create the codebase and start it.")
def setup(
    name: Optional[str] = typer.Option(
        None,
        "-n",
        "--name",
        help="Name of the new AgentOS codebase directory (e.g. `my-agentos-project`).",
        show_default=False,
    ),
    template: Optional[str] = typer.Option(
        None,
        "-t",
        "--template",
        help="Starter template for the Agno AgentOS codebase.",
        show_default=False,
    ),
    url: Optional[str] = typer.Option(
        None,
        "-u",
        "--url",
        help="URL of the starter template.",
        show_default=False,
    ),
    auto_confirm: bool = typer.Option(
        False,
        "-y",
        "--yes",
        help="Skip confirmation before deploying resources.",
    ),
    print_debug_log: bool = typer.Option(
        False,
        "-d",
        "--debug",
        help="Print debug logs.",
    ),
):
    """\b
    Set up a new AgentOS in a single command. This runs the following steps:
    \b
    1. Install missing packages (`agno`, docker support) in the current environment
    2. Create a new AgentOS codebase from a starter template (same as `ag infra create`)
    3. Start the AgentOS infrastructure (same as `ag infra up`)
    \b
    Examples:
    > ag setup                                       -> Interactive setup in the current directory
    > ag setup -t agentos-docker -n my-agentos -y    -> Non-interactive setup with the docker template
    """
    if print_debug_log:
        set_log_level_to_debug()

    from agno.infra.operator import setup_agentos

    setup_agentos(name=name, template=template, url=url, auto_confirm=auto_confirm)


agno_cli.add_typer(infra_subcommands)
