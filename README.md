# NSL Sync — Notion × Slack × Linear Cross-Platform Reporter

Dedalus-powered agent that pulls incomplete tasks from a Notion sprint database, reads a Slack channel for updates, fetches Linear issues under a team, and generates a comparison report highlighting mismatches across all three platforms.

## Setup

1. **Get API keys**
   - [Dedalus API key](https://dedaluslabs.ai/dashboard)
   - [Notion integration token](https://www.notion.so/my-integrations) — the integration must have access to your sprint pages
   - [Slack user token](https://api.slack.com/apps) — needs `channels:history`, `channels:read`, `search:read` scopes
   - Linear — uses OAuth (handled automatically on first run) or a [personal API key](https://linear.app/settings/account#api)

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Fill in your keys and sync report configuration
   ```

3. **Install dependencies**
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

## Configuration

All configuration is via `.env` or CLI flags. See `.env.example` for the full list.

| Variable | CLI flag | Description |
|----------|----------|-------------|
| `SPRINT_PAGE` | `--sprint` | Notion page title for the sprint (required) |
| `SLACK_CHANNEL` | `--channel` | Slack channel to read, without `#` (default: `general`) |
| `LINEAR_TEAM` | `--team` | Linear team name to scope issues (default: all teams) |
| — | `--week` | Week range as `M/D - M/D` (default: current week) |

## Usage

```bash
# Current week, using .env defaults
python sync_report.py

# Specific week
python sync_report.py --week "4/20 - 4/26"

# Override sprint page and Slack channel
python sync_report.py --sprint "Q2 Sprint" --channel eng-updates --team Engineering
```

On first run, if Linear requires OAuth, a browser window will open for authorization. The token is stored by the Dedalus platform for subsequent runs.

## Output

Reports are saved as `sync_report_<week>.md` (gitignored). Each report includes:

- **Summary metrics** — incomplete tasks, matches, mismatches
- **Task comparison table** — Notion status vs Linear status vs Slack activity
- **Mismatches detail** — what each platform says and suggested resolution
- **Linear issues without Notion tasks** — work tracked in Linear but missing from the sprint
- **Slack activity summary** — discussion highlights per task

## MCP Servers

| Platform | Marketplace slug |
|----------|-----------------|
| Notion   | `nickyhec/notion-mcp` |
| Slack    | `nickyhec/slack-mcp` |
| Linear   | `nickyhec/linear-mcp` |

Connection names must match the MCPServer name (e.g. `notion-mcp`, `slack-mcp`, `linear-mcp`) so the platform correctly routes credentials and stored OAuth tokens.
