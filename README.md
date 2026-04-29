# NSL Agent — Notion × Slack × Linear

A suite of Dedalus-powered agents that bridge Notion, Slack, and Linear. Each agent reads from one or more platforms and either generates a report or creates issues, keeping everything in sync.

## Agents

| Script | What it does |
|--------|-------------|
| `platforms_sync_report.py` | Pulls incomplete tasks from a Notion sprint page, reads a Slack channel for updates, fetches Linear issues, and generates a comparison report highlighting mismatches across all three platforms. |
| `slack_tasks_compilation.py` | Reads a Slack channel (including canvas and every thread) and compiles all open tasks assigned to a specific person into a markdown report with suggested Linear metadata. |
| `sync_to_linear.py` | Parses a `sprint_todos_*.md` file and creates Linear issues for rows marked "Need Sync to Linear", pulling descriptions from a Gap Analysis spreadsheet. |
| `task_to_linear.py` | Parses `slack_tasks.md` and creates Linear issues for rows marked "Need Sync to Linear", using the team/project/milestone/assignee from the table. |

## Setup

1. **Get API keys**
   - [Dedalus API key](https://dedaluslabs.ai/dashboard)
   - [Notion integration token](https://www.notion.so/my-integrations) — must have access to your sprint pages
   - [Slack user token](https://api.slack.com/apps) — needs `channels:history`, `channels:read`, `search:read` scopes
   - Linear — uses OAuth (handled automatically on first run) or a [personal API key](https://linear.app/settings/account#api)

2. **Configure environment**

```bash
cp .env.example .env
# Fill in your keys
```

3. **Install dependencies**

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

4. **Create local scripts from templates**

```bash
cp templates/platforms_sync_report.template.py platforms_sync_report.py
cp templates/slack_tasks_compilation.template.py slack_tasks_compilation.py
cp templates/task_to_linear.template.py task_to_linear.py
```

Edit each copied file to fill in your company-specific values (channel names, team names, person, etc.). The root scripts are gitignored so your local config stays private.

## Usage

### Cross-platform sync report

```bash
python platforms_sync_report.py                             # current week
python platforms_sync_report.py --week "4/20 - 4/26"        # specific week
python platforms_sync_report.py --sprint "Q2 Sprint"        # override sprint page
```

Output: `sync_report_<week>.md`

### Slack task compilation

```bash
python slack_tasks_compilation.py                           # defaults from script
python slack_tasks_compilation.py --channel general --person Alex --days 14
```

Output: `slack_tasks.md` (overwritten on each run)

### Sync to Linear (from sprint todos)

```bash
python sync_to_linear.py                                    # latest sprint_todos_*.md
python sync_to_linear.py --file sprint_todos_2026-04-28.md  # specific file
python sync_to_linear.py --dry-run                          # preview without creating
```

### Sync to Linear (from Slack tasks)

```bash
python task_to_linear.py                                    # reads slack_tasks.md
python task_to_linear.py --dry-run                          # preview without creating
```

Both Linear sync scripts find rows with "Y" in the **Need Sync to Linear** column, create issues via the Linear MCP, then update the markdown — marking **Synced to Linear** as Y and adding a **Linear Issue** column with clickable links.

## Project structure

```
.
├── connection.py                   # MCP connection config (Notion, Slack, Linear)
├── requirements.txt
├── .env.example
├── templates/
│   ├── platforms_sync_report.template.py
│   ├── slack_tasks_compilation.template.py
│   └── task_to_linear.template.py
├── platforms_sync_report.py        # ← local, gitignored
├── slack_tasks_compilation.py      # ← local, gitignored
├── sync_to_linear.py              # ← local, gitignored
└── task_to_linear.py              # ← local, gitignored
```

**Templates vs local scripts:** Template files in `templates/` use environment variables for configuration and are committed to the repo. Copy a template to the project root and hardcode your company-specific values. Root scripts are gitignored.

## MCP servers

| Platform | Marketplace slug |
|----------|-----------------|
| Notion   | `nickyhec/notion-mcp` |
| Slack    | `nickyhec/slack-mcp` |
| Linear   | `nickyhec/linear-mcp` |

On first run, if Linear or Slack requires OAuth, a browser window opens for authorization. Tokens are stored by the Dedalus platform for subsequent runs.

## Configuration reference

See `.env.example` for the full list. Key variables:

| Variable | Used by | Description |
|----------|---------|-------------|
| `DEDALUS_API_KEY` | all | Dedalus platform API key |
| `NOTION_API_KEY` | sync report | Notion integration token |
| `SLACK_ACCESS_TOKEN` | sync report, slack tasks | Slack user/bot token |
| `LINEAR_API_KEY` | all (optional) | Linear API key; leave empty for OAuth |
| `SPRINT_PAGE` | sync report | Notion sprint page title |
| `SLACK_CHANNEL` | sync report, slack tasks | Slack channel name (without `#`) |
| `LINEAR_TEAM` | sync report, task sync | Linear team name |
| `SLACK_WORKSPACE` | slack tasks | Slack workspace slug (for thread links) |
| `MODEL` | all | LLM model (default: `anthropic/claude-sonnet-4-20250514`) |

## License

MIT
