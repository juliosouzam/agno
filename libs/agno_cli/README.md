# agnoctl — the Agno CLI

The operating surface for [AgentOS](https://docs.agno.com), built for humans and coding agents equally.

The headline command connects a running AgentOS to your coding agents as an MCP server, authenticated with freshly minted service accounts:

```bash
uvx agno connect
```

This discovers the AgentOS (default `http://localhost:7777`), mints one service-account token per client (`claude-code`, `codex`, `cursor`), writes each client's MCP configuration, and verifies the connection end to end with a real MCP `tools/list` call. Re-running is safe: existing working connections are detected and skipped, broken ones are rotated.

## Commands

```bash
agno connect [--url URL] [--clients claude-code,codex,cursor] [--name NAME]
             [--scopes SCOPE ...] [--expires 90d] [--rotate | --skip-existing]
             [--server-name agno] [--project] [--json]

agno tokens create NAME [--scopes SCOPE ...] [--expires 90d|never] [--privileged] [--json]
agno tokens list [--json]
agno tokens revoke NAME [--json]

agno status [--url URL] [--json]
```

Admin credential resolution (for minting): `AGNO_ADMIN_TOKEN` env var, then `OS_SECURITY_KEY` env var, then an interactive prompt. When the target AgentOS has authorization disabled (local dev), minting is skipped and configs are written without credentials.

## Design rules

- Built to be driven by coding agents: `--json` on every command, deterministic exit codes (0 ok, 1 failure, 2 partial), no prompts in `--json` mode.
- Talks plain HTTP to the AgentOS REST API. Never imports the `agno` framework.
- Truthful outcomes: no command reports success it did not verify.
- Secrets never go to logs; `connect` writes tokens straight into client configs and never prints them. `tokens create` prints the plaintext exactly once.

## Plugins

Other distributions can add subcommands by exposing a `typer.Typer` under the `agno_cli.plugins` entry-point group:

```toml
[project.entry-points."agno_cli.plugins"]
infra = "agno_infra.cli:infra_app"
```
