"""The `agno` command: root Typer app and plugin loader."""

from typing import Optional

import typer

from agno_cli import __version__
from agno_cli.commands.connect import connect
from agno_cli.commands.create import create
from agno_cli.commands.lifecycle import down, restart, up
from agno_cli.commands.status import status
from agno_cli.commands.tokens import tokens_app
from agno_cli.console import print_info, print_warning

PLUGIN_GROUP = "agno_cli.plugins"

app = typer.Typer(
    name="agno",
    help="The Agno CLI: connect and operate AgentOS, built for humans and coding agents.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

app.command(name="connect")(connect)
app.command(name="create")(create)
app.command(name="status")(status)
app.add_typer(tokens_app, name="tokens")
app.command(name="up")(up)
app.command(name="down")(down)
app.command(name="restart")(restart)


def _load_plugins() -> None:
    """Mount subcommand groups exposed by other installed distributions.

    A plugin is an entry point in the `agno_cli.plugins` group resolving to a
    typer.Typer; the entry-point name becomes the subcommand name. A broken plugin
    must never take the core CLI down, so failures are reported and skipped.
    """
    from importlib.metadata import entry_points

    try:
        plugin_entry_points = entry_points(group=PLUGIN_GROUP)
    except TypeError:  # Python 3.9: entry_points() takes no kwargs and returns a dict
        plugin_entry_points = entry_points().get(PLUGIN_GROUP, [])  # type: ignore[arg-type,attr-defined]

    for entry_point in plugin_entry_points:
        try:
            plugin = entry_point.load()
        except Exception as e:
            print_warning("Skipping CLI plugin '" + entry_point.name + "': " + str(e))
            continue
        if isinstance(plugin, typer.Typer):
            app.add_typer(plugin, name=entry_point.name)
        else:
            print_warning("Skipping CLI plugin '" + entry_point.name + "': not a typer.Typer")


_load_plugins()


def _version_callback(value: bool) -> None:
    if value:
        print_info("agnoctl " + __version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True, help="Print the CLI version and exit."
    ),
) -> None:
    pass


if __name__ == "__main__":
    app()
