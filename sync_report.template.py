"""Cross-platform sync report — Notion × Slack × Linear.

Searches incomplete tasks from a Notion sprint database, reads a
Slack channel for relevant updates, pulls Linear projects/issues
under a team, then generates a comparison table as a markdown file.

Defaults to the current week (Monday–Sunday) unless overridden.

Copy this file to sync_report.py and update SPRINT_PAGE, SLACK_CHANNEL,
and LINEAR_TEAM with your company-specific values.

Usage:
    python sync_report.py                                          # current week
    python sync_report.py --week "3/30 - 4/5"                      # specific week
    python sync_report.py --sprint "My Sprint Page"                 # custom sprint
    python sync_report.py --channel general --team Engineering      # custom sources
"""

import asyncio
import os
import re
import sys
import webbrowser
from datetime import datetime, timedelta, timezone

from dedalus_labs import AsyncDedalus, AuthenticationError, DedalusRunner
from dedalus_labs.utils.stream import stream_async
from dotenv import load_dotenv

load_dotenv()

from connection import notion_secrets, slack_secrets, linear_secrets

MODEL = os.getenv("MODEL", "anthropic/claude-sonnet-4-20250514")
TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "900"))

NOTION_MCP = os.getenv("NOTION_MCP_SERVER", "nickyhec/notion-mcp")
SLACK_MCP = os.getenv("SLACK_MCP_SERVER", "nickyhec/slack-mcp")
LINEAR_MCP = os.getenv("LINEAR_MCP_SERVER", "nickyhec/linear-mcp")

SPRINT_PAGE = os.getenv("SPRINT_PAGE", "")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "general")
LINEAR_TEAM = os.getenv("LINEAR_TEAM", "")

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def _current_week() -> tuple[datetime, datetime, str]:
    """Return (monday, sunday, label) for the current week."""
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6)
    label = f"{monday.month}/{monday.day} - {sunday.month}/{sunday.day}"
    return monday, sunday, label


def _parse_week_label(label: str) -> tuple[datetime, datetime]:
    """Parse a 'M/D - M/D' label into (start, end) datetimes for the current year."""
    parts = label.split("-")
    if len(parts) != 2:
        raise ValueError(f"Week label must be 'M/D - M/D', got: {label}")
    now = datetime.now(timezone.utc)
    start_parts = parts[0].strip().split("/")
    end_parts = parts[1].strip().split("/")
    start = datetime(now.year, int(start_parts[0]), int(start_parts[1]), tzinfo=timezone.utc)
    end = datetime(now.year, int(end_parts[0]), int(end_parts[1]), tzinfo=timezone.utc)
    return start, end


def _unix_ts(dt: datetime) -> str:
    """Return Unix timestamp string for a datetime."""
    return str(int(dt.timestamp()))


def build_prompt(sprint_page: str, week_label: str, today: str,
                 week_start: datetime, week_end: datetime,
                 slack_channel: str, linear_team: str) -> str:
    slack_oldest = _unix_ts(week_start)
    day_after_end = week_end + timedelta(days=1)
    slack_latest = _unix_ts(day_after_end)

    start_str = week_start.strftime("%B %d")
    end_str = week_end.strftime("%B %d, %Y")

    linear_scope = (
        f'team "{linear_team}" (ALL projects and issues)'
        if linear_team
        else "ALL teams, projects, and issues"
    )

    return f"""You are a cross-platform project sync analyst. You must gather data
from Notion, Slack, and Linear using the available MCP tools, then produce a
comparison report focused on MISMATCHES between platforms.

IMPORTANT: You MUST first gather ALL data using tools before writing ANY output.
Do NOT start writing the report until you have completed all data gathering steps.
Only use tools that start with "mcp-" prefix — those are the available MCP tools.

Today's date: {today}

## Context
- Sprint page on Notion: "{sprint_page}"
- Week section / database: "Week of {week_label}"
- Slack channel: #{slack_channel}
- Slack time window: {start_str} – {end_str} ONLY
- Linear scope: {linear_scope}

---

## DATA GATHERING (do all of this BEFORE writing any output)

### Notion — Find incomplete tasks
1. Search for "{sprint_page}" to find the sprint page.
2. Fetch the page and its content/child blocks.
3. Look for a database or section titled "Week of {week_label}".
4. Query the database to get all rows.
5. Identify tasks NOT marked as complete/done.

### Slack — Read #{slack_channel} ({start_str} – {end_str} ONLY)
1. Search for the "{slack_channel}" channel.
2. Get channel history with oldest="{slack_oldest}" and latest="{slack_latest}", limit=500.
   Paginate if needed.
3. Scan messages for mentions of tasks found in Notion.
4. Get thread replies for relevant threads.

### Linear — {linear_scope}
1. List teams{f' to find "{linear_team}"' if linear_team else ''}.
2. List ALL projects and ALL issues{f' under "{linear_team}"' if linear_team else ''} (paginate fully).

---

## AFTER gathering all data, output a markdown report with this structure:

# Cross-Platform Sync Report
**Sprint:** {sprint_page}
**Week:** {week_label}
**Generated:** {today}

## Summary
| Metric | Count |
|--------|-------|
| Incomplete tasks (Notion) | [N] |
| Tasks with Slack updates | [N] |
| Tasks with no Slack mention | [N] |
| Matching Linear issues found | [N] |
| Linear issues without Notion task | [N] |
| Status mismatches | [N] |
| Total mismatches found | [N] |

## Task Comparison Table
| # | Task (Notion) | Notion Status | Linear Issue | Linear Status | Slack Updates | Mismatch |
|---|---------------|---------------|--------------|---------------|---------------|----------|

Include ALL incomplete Notion tasks. Use "—" for missing data.

## Mismatches Detail
For each mismatch: what the discrepancy is, what each platform says,
and suggested resolution.

## Linear Issues Without Notion Task
| # | Linear Issue | Title | Status | Assignee | Project |
|---|-------------|-------|--------|----------|---------|

## Slack Activity Summary
For tasks with Slack mentions in the {week_label} window, summarize
the discussion (2-3 sentences max per task).

## Rules
- Do NOT output anything until all data gathering is complete.
- Start output with `# Cross-Platform Sync Report`.
- Use "—" for missing data.
- Focus on surfacing MISMATCHES."""


def extract_report(raw: str) -> str:
    match = re.search(r"(# Cross-Platform Sync Report.+)", raw, re.DOTALL)
    if match:
        return match.group(1).strip() + "\n"
    return raw.strip() + "\n"


def _extract_connect_url(err: AuthenticationError) -> str | None:
    body = err.body if isinstance(err.body, dict) else {}
    return body.get("connect_url") or body.get("detail", {}).get("connect_url")


def _prompt_oauth(service: str, url: str) -> None:
    print(f"\n{service} OAuth required. Opening browser...")
    print(f"   URL: {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    if sys.stdin.isatty():
        input(f"\n   Press Enter after completing {service} OAuth...")
    else:
        wait = int(os.getenv("OAUTH_WAIT_SECONDS", "30"))
        print(f"   Non-interactive mode — waiting {wait}s for OAuth completion...")
        import time
        time.sleep(wait)


async def _ensure_linear_oauth(runner: DedalusRunner) -> None:
    """Probe Linear with credentials; if OAuth is needed, trigger browser flow."""
    print("Checking Linear auth...", flush=True)

    async def _probe(creds=None):
        kwargs = dict(
            input="List all teams. Return only team names, one per line.",
            model=MODEL,
            mcp_servers=[LINEAR_MCP],
            stream=True,
            max_steps=5,
        )
        if creds:
            kwargs["credentials"] = creds
        stream = runner.run(**kwargs)
        result = await stream_async(stream)
        return result.content

    try:
        result = await _probe()
    except AuthenticationError as err:
        url = _extract_connect_url(err)
        if not url:
            raise
        _prompt_oauth("Linear", url)
        result = await _probe()

    print(f"  Linear: {result[:100].strip()}")


async def run_sync(sprint_page: str, week_label: str,
                   week_start: datetime, week_end: datetime,
                   slack_channel: str, linear_team: str) -> str:
    client = AsyncDedalus(timeout=TIMEOUT)
    runner = DedalusRunner(client)

    today = datetime.now().strftime("%Y-%m-%d")
    prompt = build_prompt(sprint_page, week_label, today, week_start, week_end,
                          slack_channel, linear_team)

    mcp_servers = [NOTION_MCP, SLACK_MCP, LINEAR_MCP]
    credentials = [notion_secrets, slack_secrets]

    print(f"Cross-Platform Sync Report")
    print(f"{'=' * 55}")
    print(f"  Sprint:   {sprint_page}")
    print(f"  Week:     {week_label}")
    print(f"  Channel:  #{slack_channel}")
    print(f"  Team:     {linear_team or '(all)'}")
    print(f"  Model:    {MODEL}")
    print(f"{'=' * 55}\n")

    await _ensure_linear_oauth(runner)
    print()

    print("Running full sync report...", flush=True)
    stream = runner.run(
        input=prompt,
        model=MODEL,
        mcp_servers=mcp_servers,
        credentials=credentials,
        stream=True,
        max_steps=50,
    )
    result = await stream_async(stream)

    print()
    return result.content


async def main() -> None:
    if not os.getenv("DEDALUS_API_KEY"):
        print("Error: DEDALUS_API_KEY not set. See .env")
        sys.exit(1)

    sprint_page = SPRINT_PAGE
    slack_channel = SLACK_CHANNEL
    linear_team = LINEAR_TEAM
    week_start, week_end, week_label = _current_week()

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--sprint" and i + 1 < len(args):
            sprint_page = args[i + 1]; i += 2
        elif args[i] == "--week" and i + 1 < len(args):
            week_label = args[i + 1]
            week_start, week_end = _parse_week_label(week_label)
            i += 2
        elif args[i] == "--channel" and i + 1 < len(args):
            slack_channel = args[i + 1]; i += 2
        elif args[i] == "--team" and i + 1 < len(args):
            linear_team = args[i + 1]; i += 2
        else:
            i += 1

    if not sprint_page:
        print("Error: no sprint page specified. Use --sprint or set SPRINT_PAGE in .env")
        sys.exit(1)

    raw = await run_sync(sprint_page, week_label, week_start, week_end,
                         slack_channel, linear_team)
    report = extract_report(raw)

    if report.strip():
        week_slug = week_label.replace("/", "-").replace(" ", "")
        filename = os.path.join(OUTPUT_DIR, f"sync_report_{week_slug}.md")
        with open(filename, "w") as f:
            f.write(report)
        print(f"\nReport saved to: {filename}")


if __name__ == "__main__":
    asyncio.run(main())
