"""`agno create`: scaffold a new AgentOS project from a starter template.

The mechanism is deliberately simple (inherited from `ag infra create`): shallow-clone
the template repository, strip its git history, and copy example secrets into place.
No registry file is kept — commands operate on the current directory.

With no arguments (or a missing name/template), an interactive TTY prompts for the
starter template and project directory — the same flow as the legacy `ag infra create`.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from agnoctl.commands._common import handle_cli_error, stdin_is_interactive, validate_project_name
from agnoctl.console import emit_json, print_info, print_success, print_warning
from agnoctl.errors import CLIError

TEMPLATES: Dict[str, str] = {
    "agentos-docker": "https://github.com/agno-agi/agentos-docker",
    "agentos-aws": "https://github.com/agno-agi/agentos-aws",
    "agentos-fly": "https://github.com/agno-agi/agentos-fly",
    "agentos-gcp": "https://github.com/agno-agi/agentos-gcp",
    "agentos-railway": "https://github.com/agno-agi/agentos-railway",
}

DEFAULT_TEMPLATE = "agentos-docker"

# Stable display order: docker first (the default), then the rest alphabetically.
TEMPLATE_CHOICES: List[str] = [DEFAULT_TEMPLATE] + sorted(k for k in TEMPLATES if k != DEFAULT_TEMPLATE)

GIT_TIMEOUT = 300.0


def _clone(repo_url: str, target: Path) -> None:
    if shutil.which("git") is None:
        raise CLIError("git is required to create a project from a template.", hint="Install git and re-run.")
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(target)],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise CLIError("git clone timed out after " + str(int(GIT_TIMEOUT)) + "s: " + repo_url)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise CLIError("git clone failed: " + (detail or repo_url))
    shutil.rmtree(target / ".git", ignore_errors=True)


def _prompt_template() -> str:
    """Numbered template menu; Enter takes the default (agentos-docker)."""
    print_info("Select starter template or press Enter for default (" + DEFAULT_TEMPLATE + ")")
    for index, key in enumerate(TEMPLATE_CHOICES, start=1):
        print_info("  [" + str(index) + "] " + key)
    while True:
        raw = str(typer.prompt("Chosen Template", default="1")).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(TEMPLATE_CHOICES):
            return TEMPLATE_CHOICES[int(raw) - 1]
        print_warning("Enter a number between 1 and " + str(len(TEMPLATE_CHOICES)) + ".")


def _prompt_project_name(default: str) -> str:
    return str(typer.prompt("Project Directory", default=default)).strip()


def _default_name_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1].split(".")[0] or "agentos-project"


def _resolve_create_inputs(
    name: Optional[str],
    template: Optional[str],
    template_url: Optional[str],
    json_output: bool,
) -> tuple[str, Optional[str]]:
    """Fill in name/template via prompts when running interactively, else apply defaults.

    Mirrors legacy `ag infra create`: template first (so the name default can follow it),
    then project directory. Automation (--json / non-TTY) requires an explicit name and
    falls back to agentos-docker when -t is omitted.
    """
    interactive = stdin_is_interactive() and not json_output

    if template_url is None and template is None:
        if interactive:
            template = _prompt_template()
        else:
            template = DEFAULT_TEMPLATE

    if name is None:
        if not interactive:
            raise CLIError(
                "Project name is required.",
                hint="Pass a directory name: agno create <name> [-t " + DEFAULT_TEMPLATE + "]",
            )
        if template_url is not None:
            default_name = _default_name_from_url(template_url)
        else:
            default_name = template or DEFAULT_TEMPLATE
        name = _prompt_project_name(default_name)

    return name, template


def create(
    name: Optional[str] = typer.Argument(None, help="Directory name for the new AgentOS project."),
    template: Optional[str] = typer.Option(
        None,
        "--template",
        "-t",
        help="Starter template: " + ", ".join(TEMPLATE_CHOICES) + ". Prompts when omitted on a TTY.",
    ),
    template_url: Optional[str] = typer.Option(
        None, "--url", "-u", help="Clone from a custom template repository URL instead."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit a single JSON document for machine consumption."),
) -> None:
    """Create a new AgentOS project from a starter template."""
    try:
        name, template = _resolve_create_inputs(name, template, template_url, json_output)
        # template is only None when --url was passed; _create tolerates that.
        payload = _create(name=name, template=template or DEFAULT_TEMPLATE, template_url=template_url)
    except CLIError as e:
        raise handle_cli_error(e, json_output)

    if json_output:
        emit_json(payload)
        return
    print_success("Created " + str(payload["path"]) + " from " + str(payload["template"]))
    print_info("")
    print_info("Next steps:")
    print_info("  cd " + name)
    print_info("  cp example.env .env  # then fill in your secrets")
    print_info("  agno up              # start it with docker compose")
    print_info("  agno connect         # make it available in your coding agents")


def _create(name: str, template: str, template_url: Optional[str]) -> Dict[str, Any]:
    validate_project_name(name)
    target = Path.cwd() / name
    if target.exists():
        raise CLIError(
            "The directory " + str(target) + " already exists.",
            hint="Pick a different name or remove the existing directory.",
        )

    if template_url:
        repo_url = template_url
        template_label = template_url
    else:
        repo_url = TEMPLATES.get(template, "")
        if not repo_url:
            raise CLIError(
                "Unknown template: " + template,
                hint="Available templates: " + ", ".join(TEMPLATE_CHOICES) + ", or pass --url for a custom repo.",
            )
        template_label = template

    _clone(repo_url, target)
    return {"path": str(target), "template": template_label}
