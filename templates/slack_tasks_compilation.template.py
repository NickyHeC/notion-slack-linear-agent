"""Compile open/ongoing tasks from a Slack channel for a specific person.

Reads channel messages, threads, and the channel canvas via the Slack MCP,
then queries Linear for project/milestone context to suggest Linear config
for each task. Outputs a markdown report with a task table and a list of
unclosed threads.

Copy this file to ../slack_tasks_compilation.py and update SLACK_CHANNEL,
PERSON, DEFAULT_DAYS, and SLACK_WORKSPACE with your company-specific values.

Usage:
    python slack_tasks_compilation.py
    python slack_tasks_compilation.py --channel general --person Alice
    python slack_tasks_compilation.py --days 14
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

from connection import slack_secrets

MODEL = os.getenv("MODEL", "anthropic/claude-sonnet-4-20250514")
TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "900"))
SLACK_MCP = os.getenv("SLACK_MCP_SERVER", "nickyhec/slack-mcp")
LINEAR_MCP = os.getenv("LINEAR_MCP_SERVER", "nickyhec/linear-mcp")

SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "general")
PERSON = os.getenv("SLACK_TASKS_PERSON", "")
DEFAULT_DAYS = int(os.getenv("SLACK_TASKS_DAYS", "7"))
SLACK_WORKSPACE = os.getenv("SLACK_WORKSPACE", "")

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def build_prompt(channel: str, person: str, days: int, today: str,
                 oldest_ts: str, workspace: str) -> str:
    return f"""You are a task-extraction agent. Your job is to read an entire Slack
channel — including every thread and the channel canvas — then compile all
open or ongoing tasks assigned to a specific person.

Today's date: {today}

## DATA GATHERING — complete ALL steps before writing ANY output

### Step 1 — Find the channel
Use mcp-search_channels or mcp-list_channels to find "#{channel}".
Note the channel ID (starts with C).

### Step 2 — Read the channel canvas
If the channel has a canvas or pinned document, read it using the
available canvas/bookmark tools. Canvases often contain standing task
lists and project trackers.

### Step 3 — Read full channel history
Use mcp-get_channel_history with the channel ID, oldest="{oldest_ts}",
limit=200. Paginate (using cursor/next_cursor) until you have ALL
messages from the last {days} days.

### Step 4 — Read EVERY thread
For each message that has a thread (reply_count > 0 or thread_ts is set),
call mcp-get_thread_replies with the channel ID and the message ts.
This is critical — status updates and completions live in threads.

### Step 5 — Identify {person}'s tasks
Scan all messages, thread replies, AND the canvas for:
- Tasks explicitly assigned to {person} (mentions of "{person.lower()}", "@{person.lower()}", etc.)
- Action items {person} volunteered for or was asked to do
- Items where {person} is the assignee, owner, or responsible party
- General to-dos that involve {person}

### Step 6 — Determine status of each item
For each task found, read through its thread replies to determine:
- Is it marked done / completed / resolved?  →  mark as "Completed"
- Is it still open with no progress?          →  mark as "Open"
- Is there partial progress?                  →  mark as "In Progress"
- Is someone blocking or waiting?             →  mark as "Blocked"

### Step 7 — Query Linear for context
Use the Linear MCP to:
1. List all teams — note the team names and IDs.
2. List projects under each relevant team.
3. List milestones under each relevant project.
This context will be used to suggest appropriate Linear Teamspace, Project,
and Milestone for each task.

### Step 8 — Find unclosed threads
Identify threads from the last {days} days where:
- {person} was asked a question or given an action item
- {person} hasn't replied yet, OR
- The thread is still unresolved / awaiting a response
- {person} is the obvious next person who needs to act

---

## OUTPUT — write this as your final text response

Start your output with "# Slack Tasks for {person}" and use this EXACT structure:

# Slack Tasks for {person}

**Channel:** #{channel}
**Period:** Last {days} days (through {today})
**Generated:** {today}

## Summary
| Metric | Count |
|--------|-------|
| Open / in-progress tasks | [N] |
| Unclosed threads awaiting {person} | [N] |
| Completed (excluded from task table) | [N] |

## Tasks

| # | Task Name | Assigned by | Date Assigned | Thread Link | Status | Linear Assignee | Linear Teamspace | Project | Milestone | Priority | Need Sync to Linear | Synced to Linear |
|---|-----------|-------------|---------------|-------------|--------|-----------------|------------------|---------|-----------|----------|---------------------|------------------|

For each row:
- **Task Name**: concise description of what needs to be done
- **Assigned by**: who posted or assigned the task (use display name and Slack user ID if available, e.g. "Prakash (U09ANRGCP6W)")
- **Date Assigned**: YYYY-MM-DD when it was posted
- **Thread Link**: clickable Slack link in the format [thread](https://{workspace}.slack.com/archives/CHANNEL_ID/pTIMESTAMP) where TIMESTAMP is the message ts with the dot removed. This MUST be a clickable markdown link.
- **Status**: "Open", "In Progress", or "Blocked" (exclude completed items from this table)
- **Linear Assignee**: suggest who should own this in Linear based on who it's assigned to in Slack — use their first name
- **Linear Teamspace**: suggest the most appropriate Linear team based on the task content and the teams you found in Step 7
- **Project**: suggest the most appropriate Linear project based on the task content and the projects you found in Step 7
- **Milestone**: suggest the most appropriate Linear milestone based on the task timing and urgency
- **Priority**: leave as "—" (empty)
- **Need Sync to Linear**: leave as "—" (empty)
- **Synced to Linear**: leave as "—" (empty)

Only include tasks that are Open, In Progress, or Blocked. Do NOT include completed tasks in this table.

## Unclosed Threads

| # | Topic | Last message from | Date | Thread Link |
|---|-------|-------------------|------|-------------|

These are threads where someone asked {person} a question or requested an
update and {person} hasn't replied yet, OR where {person} is the obvious
next person who needs to act. The Thread Link column must use clickable
markdown links in the same format as above:
[thread](https://{workspace}.slack.com/archives/CHANNEL_ID/pTIMESTAMP)

## Recently Completed (for reference)

| # | Task | Completed by | Date |
|---|------|-------------|------|

List tasks that were completed in the last {days} days so {person} has context.

## Rules
- Do NOT output anything until ALL data gathering is complete.
- Read EVERY thread — do not skip any.
- A task is only "done" if there's an explicit completion message in the thread.
- If unsure whether something is done, include it as open.
- Sort tables by date, newest first.
- Use "—" for missing data.
- ALL thread links MUST be clickable markdown links."""


def extract_report(raw: str, person: str) -> str:
    pattern = rf"(# Slack Tasks for {re.escape(person)}.+)"
    match = re.search(pattern, raw, re.DOTALL)
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
        print(f"   Non-interactive mode — waiting {wait}s...")
        import time
        time.sleep(wait)


async def _ensure_linear_oauth(runner: DedalusRunner) -> None:
    print("Checking Linear auth...", flush=True)

    async def _probe():
        stream = runner.run(
            input="List all teams. Return only team names, one per line.",
            model=MODEL,
            mcp_servers=[LINEAR_MCP],
            stream=True,
            max_steps=5,
        )
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


async def main() -> None:
    if not os.getenv("DEDALUS_API_KEY"):
        print("Error: DEDALUS_API_KEY not set. See .env")
        sys.exit(1)

    channel = SLACK_CHANNEL
    person = PERSON
    days = DEFAULT_DAYS
    workspace = SLACK_WORKSPACE

    if not person:
        print("Error: no person specified. Use --person or set SLACK_TASKS_PERSON in .env")
        sys.exit(1)

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--channel" and i + 1 < len(args):
            channel = args[i + 1]; i += 2
        elif args[i] == "--person" and i + 1 < len(args):
            person = args[i + 1]; i += 2
        elif args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        elif args[i] == "--workspace" and i + 1 < len(args):
            workspace = args[i + 1]; i += 2
        else:
            i += 1

    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=days)
    oldest_ts = str(int(oldest.timestamp()))
    today = now.strftime("%Y-%m-%d")

    prompt = build_prompt(channel, person, days, today, oldest_ts, workspace)

    client = AsyncDedalus(timeout=TIMEOUT)
    runner = DedalusRunner(client)

    print("Slack Task Compilation")
    print("=" * 55)
    print(f"  Channel:   #{channel}")
    print(f"  Person:    {person}")
    print(f"  Lookback:  {days} days")
    print(f"  Workspace: {workspace}")
    print(f"  Model:     {MODEL}")
    print("=" * 55)
    print(flush=True)

    await _ensure_linear_oauth(runner)
    print()

    async def _run():
        stream = runner.run(
            input=prompt,
            model=MODEL,
            mcp_servers=[SLACK_MCP, LINEAR_MCP],
            credentials=[slack_secrets],
            stream=True,
            max_steps=80,
        )
        return await stream_async(stream)

    print("Running agent...", flush=True)
    try:
        result = await _run()
    except AuthenticationError as err:
        url = _extract_connect_url(err)
        if not url:
            raise
        _prompt_oauth("Slack", url)
        result = await _run()

    raw = result.content
    report = extract_report(raw, person)

    if report.strip():
        filename = os.path.join(OUTPUT_DIR, "slack_tasks.md")
        with open(filename, "w") as f:
            f.write(report)
        print(f"\nSaved to: {filename}")


if __name__ == "__main__":
    asyncio.run(main())
