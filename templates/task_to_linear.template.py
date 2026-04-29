"""Sync Slack tasks to Linear from a slack_tasks.md file.

Reads slack_tasks.md, finds rows marked with Y (or yes/confirmed) in the
"Need Sync to Linear" column, creates Linear issues using the team/project/
milestone/assignee specified in the table, then updates the markdown with
issue links and marks them synced.

Copy this file to ../task_to_linear.py and update any company-specific
defaults (DEFAULT_TEAM) as needed.

Usage:
    python task_to_linear.py                          # reads slack_tasks.md
    python task_to_linear.py --file slack_tasks.md     # explicit file
    python task_to_linear.py --dry-run                 # preview without creating
"""

import asyncio
import os
import sys
import webbrowser

from dedalus_labs import AsyncDedalus, AuthenticationError, DedalusRunner
from dedalus_labs.utils.stream import stream_async
from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("MODEL", "anthropic/claude-sonnet-4-20250514")
TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "900"))
LINEAR_MCP = os.getenv("LINEAR_MCP_SERVER", "nickyhec/linear-mcp")

DEFAULT_TEAM = os.getenv("LINEAR_TEAM", "Operations")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(SCRIPT_DIR, "slack_tasks.md")

_YES_VALUES = {"y", "yes", "confirmed", "confirm", "true", "1"}

_PRIORITY_LABELS = {
    "urgent": 1, "high": 2, "medium": 3, "normal": 3, "low": 4,
}
_PRIORITY_NAMES = {0: "None", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}


def _parse_table_rows(lines: list[str], header_idx: int) -> list[dict]:
    headers_raw = [h.strip() for h in lines[header_idx].split("|")]
    headers = [h for h in headers_raw if h and h != "---"]
    rows = []
    for i in range(header_idx + 2, len(lines)):
        line = lines[i].strip()
        if not line or not line.startswith("|"):
            break
        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c != ""]
        if len(cells) >= len(headers):
            rows.append({headers[j]: cells[j] for j in range(len(headers))})
    return rows


def parse_slack_tasks(filepath: str) -> list[dict]:
    """Parse slack_tasks.md and return items needing sync."""
    with open(filepath) as f:
        lines = f.readlines()

    items = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("| #") and "Need Sync to Linear" in stripped:
            rows = _parse_table_rows(lines, i)
            for row in rows:
                need_sync = row.get("Need Sync to Linear", "").strip().lower()
                synced = row.get("Synced to Linear", "").strip().lower()
                if need_sync in _YES_VALUES and synced not in _YES_VALUES:
                    items.append(row)
    return items


def _resolve_priority(label: str) -> tuple[int, str]:
    key = label.strip().lower()
    val = _PRIORITY_LABELS.get(key, 3)
    return val, _PRIORITY_NAMES[val]


def build_description(item: dict) -> str:
    """Build a Linear issue description from a slack_tasks row."""
    parts = []

    thread_link = item.get("Thread Link", "").strip()
    if thread_link and thread_link != "—":
        parts.append(f"Source: {thread_link}")
        parts.append("")

    assigned_by = item.get("Assigned by", "").strip()
    date_assigned = item.get("Date Assigned", "").strip()
    status = item.get("Status", "").strip()

    if assigned_by and assigned_by != "—":
        parts.append(f"**Assigned by:** {assigned_by}")
    if date_assigned and date_assigned != "—":
        parts.append(f"**Date:** {date_assigned}")
    if status and status != "—":
        parts.append(f"**Slack status:** {status}")

    return "\n".join(parts)


def build_prompt(issues: list[dict]) -> str:
    issue_blocks = []
    for idx, iss in enumerate(issues, 1):
        block = f"""### Issue {idx}
- **Title:** {iss["title"]}
- **Team:** {iss["team"]}
- **Project:** {iss["project"] or "(none)"}
- **Assignee:** {iss["assignee"]}
- **Priority:** {iss["priority"]} ({iss["priority_name"]})
- **Milestone:** {iss["milestone"] or "(none)"}
- **State:** Todo
- **Description:**

{iss["description"]}"""
        issue_blocks.append(block)

    issues_text = "\n\n---\n\n".join(issue_blocks)

    return f"""You are a Linear issue creation agent. Your job is to create exactly
{len(issues)} issues in Linear using the available MCP tools.

IMPORTANT: Create ALL issues. Do NOT skip any. Do NOT modify the titles,
descriptions, assignees, or priorities — use them exactly as specified.

## Instructions

For each issue below, call `save_issue` with these parameters:
- **title**: as specified
- **team**: as specified
- **project**: as specified (skip if "(none)")
- **assignee**: as specified (use the name directly)
- **priority**: as specified (0=None, 1=Urgent, 2=High, 3=Medium/Normal, 4=Low)
- **milestone**: as specified (skip if "(none)")
- **state**: Todo
- **description**: as specified — use literal newlines, do NOT escape them

## Issues to Create

{issues_text}

---

## Output Format

After creating ALL issues, output a summary table in EXACTLY this format.
The FIRST line of your output MUST be the heading. No preamble.

# Linear Sync Results

| Row | Identifier | URL |
|-----|------------|-----|
| [row_num] | [OPS-XXX] | [full Linear URL] |

Include one row per issue created, in the same order as above.
The "Row" column must contain only the row number from the tasks table (e.g. 1, 2, 3).

## Rules
- Do NOT output anything until ALL issues are created.
- Create issues one at a time, in order.
- Use the exact title, description, assignee, priority, and milestone specified.
- The first line of your output MUST be `# Linear Sync Results`."""


def update_markdown(filepath: str, updates: list[dict]) -> None:
    """Mark Synced to Linear = Y and add a Linear Issue column with links."""
    with open(filepath) as f:
        lines = f.readlines()

    update_map = {u["row_num"]: u for u in updates}
    add_col = "Linear Issue" not in "".join(lines)
    out: list[str] = []
    in_tasks_table = False
    current_data_row = 0

    for line in lines:
        stripped = line.rstrip("\n")

        if not stripped.startswith("|"):
            out.append(line)
            in_tasks_table = False
            current_data_row = 0
            continue

        is_header = "Need Sync to Linear" in stripped and "---" not in stripped

        if is_header:
            in_tasks_table = True
            current_data_row = 0
            if add_col:
                out.append(stripped + " Linear Issue |\n")
            else:
                out.append(line)
            continue

        if in_tasks_table and all(
            c.strip() == "" or set(c.strip()) <= {"-"}
            for c in stripped.split("|")[1:-1]
        ) and current_data_row == 0:
            if add_col:
                out.append(stripped + " --- |\n")
            else:
                out.append(line)
            continue

        if not in_tasks_table:
            out.append(line)
            continue

        first_cell = stripped.split("|")[1].strip() if len(stripped.split("|")) > 1 else ""
        if first_cell.isdigit():
            current_data_row = int(first_cell)
            if current_data_row in update_map:
                upd = update_map[current_data_row]
                link = f"[{upd['identifier']}]({upd['url']})"
                cells = stripped.split("|")

                for j in range(len(cells) - 1, 0, -1):
                    if cells[j].strip() in ("—", "-", ""):
                        prev = cells[j - 1].strip().lower() if j > 1 else ""
                        if prev in _YES_VALUES or "need sync" in prev.lower():
                            continue
                        cells[j] = " Y "
                        break

                if add_col:
                    cells.insert(len(cells) - 1, f" {link} ")

                out.append("|".join(cells) + "\n")
                continue
            elif add_col:
                cells = stripped.split("|")
                cells.insert(len(cells) - 1, " — ")
                out.append("|".join(cells) + "\n")
                continue

        out.append(line)

    with open(filepath, "w") as f:
        f.writelines(out)


def extract_results(raw: str) -> list[dict]:
    """Parse the agent's output table into a list of {row_num, identifier, url}."""
    results = []
    in_table = False
    for line in raw.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# Linear Sync Results"):
            in_table = True
            continue
        if not in_table:
            continue
        if stripped.startswith("|") and "---" not in stripped and "Row" not in stripped:
            cells = [c.strip() for c in stripped.split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 3 and cells[0].isdigit():
                results.append({
                    "row_num": int(cells[0]),
                    "identifier": cells[1],
                    "url": cells[2],
                })
    return results


def _extract_connect_url(err: AuthenticationError) -> str | None:
    body = err.body if isinstance(err.body, dict) else {}
    return body.get("connect_url") or body.get("detail", {}).get("connect_url")


def _prompt_oauth(url: str) -> None:
    print("\nLinear OAuth required. Opening browser...")
    print(f"   URL: {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    if sys.stdin.isatty():
        input("\n   Press Enter after completing Linear OAuth...")
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
        _prompt_oauth(url)
        result = await _probe()

    print(f"  Linear: {result[:100].strip()}")


async def main() -> None:
    if not os.getenv("DEDALUS_API_KEY"):
        print("Error: DEDALUS_API_KEY not set. See .env")
        sys.exit(1)

    filepath = DEFAULT_FILE
    dry_run = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--file" and i + 1 < len(args):
            filepath = args[i + 1]
            if not os.path.isabs(filepath):
                filepath = os.path.join(SCRIPT_DIR, filepath)
            i += 2
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            i += 1

    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found. Run slack_tasks_compilation.py first.")
        sys.exit(1)

    print("Slack Tasks → Linear Sync")
    print("=" * 55)
    print(f"  File:     {os.path.basename(filepath)}")
    print(f"  Model:    {MODEL}")
    print(f"  MCP:      {LINEAR_MCP}")
    print(f"  Dry run:  {dry_run}")
    print("=" * 55)

    items = parse_slack_tasks(filepath)
    if not items:
        print("\nNo items needing sync found.")
        return

    print(f"\nFound {len(items)} items to sync:")

    issue_specs: list[dict] = []
    for item in items:
        row_num = int(item.get("#", "0").strip())
        task_name = item.get("Task Name", "").strip()
        assignee = item.get("Linear Assignee", "").strip()
        team = item.get("Linear Teamspace", "").strip()
        project = item.get("Project", "").strip()
        milestone = item.get("Milestone", "").strip()
        priority_label = item.get("Priority", "").strip()

        pri_val, pri_name = _resolve_priority(priority_label)
        description = build_description(item)

        if team in ("—", "-", ""):
            team = DEFAULT_TEAM
        if project in ("—", "-", ""):
            project = None
        if milestone in ("—", "-", ""):
            milestone = None
        if assignee in ("—", "-", ""):
            assignee = ""

        spec = {
            "row_num": row_num,
            "title": task_name,
            "description": description,
            "assignee": assignee,
            "team": team,
            "project": project,
            "milestone": milestone,
            "priority": pri_val,
            "priority_name": pri_name,
        }
        issue_specs.append(spec)

        print(f"\n  #{row_num} {task_name[:60]}")
        print(f"      Team: {team} | Assignee: {assignee or '(none)'} | Priority: {pri_name}")
        if project:
            print(f"      Project: {project}", end="")
            if milestone:
                print(f" | Milestone: {milestone}")
            else:
                print()

    if dry_run:
        print("\n\n  [DRY RUN] No issues created.")
        return

    prompt = build_prompt(issue_specs)

    client = AsyncDedalus(timeout=TIMEOUT)
    runner = DedalusRunner(client)

    await _ensure_linear_oauth(runner)
    print()

    print("Running agent to create issues...", flush=True)

    async def _run():
        stream = runner.run(
            input=prompt,
            model=MODEL,
            mcp_servers=[LINEAR_MCP],
            stream=True,
            max_steps=200,
        )
        return await stream_async(stream)

    try:
        result = await _run()
    except AuthenticationError as err:
        url = _extract_connect_url(err)
        if not url:
            raise
        _prompt_oauth(url)
        result = await _run()

    raw = result.content
    print()

    updates = extract_results(raw)
    if updates:
        print(f"Agent created {len(updates)} issues. Updating markdown...")
        update_markdown(filepath, updates)
        print(f"  Updated {os.path.basename(filepath)}")
    else:
        print("Warning: could not parse issue results from agent output.")
        print("Raw output saved for manual review.")
        raw_path = os.path.join(SCRIPT_DIR, "task_to_linear_output.md")
        with open(raw_path, "w") as f:
            f.write(raw)
        print(f"  Saved to: {raw_path}")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
