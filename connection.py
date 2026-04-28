"""Connection configuration for Notion, Slack, and Linear MCP servers.

Connection names must match the MCPServer name on the platform so that
stored OAuth tokens and client-provided credentials are indexed under
the same key the tool uses at lookup time.

The SDK's slug_to_connection_name converts "nickyhec/linear-mcp" to
"nickyhec-linear-mcp", which won't match "linear-mcp". We patch
_credentials_for_server to fall back to matching by slug suffix.
"""

import os

from dedalus_mcp.auth import Connection, SecretKeys, SecretValues
from dotenv import load_dotenv

load_dotenv()

# --- Patch SDK credential routing ---

import dedalus_labs.lib.mcp.request as _mcp_request

_orig_creds_for_server = _mcp_request._credentials_for_server

def _patched_credentials_for_server(name, all_creds):
    """Match by slug-derived name first, then try slug suffix fuzzy match."""
    result = _orig_creds_for_server(name, all_creds)
    if result is not None:
        return result
    if "/" in name:
        server_part = name.split("/", 1)[1]
        for cred_name, cred_blob in all_creds.items():
            if (cred_name == server_part
                    or server_part.startswith(cred_name)
                    or cred_name.startswith(server_part)):
                return {cred_name: cred_blob}
    return None

_mcp_request._credentials_for_server = _patched_credentials_for_server

# --- Connection definitions (matching MCPServer name) ---

notion = Connection(
    name="notion-mcp",
    secrets=SecretKeys(token="NOTION_API_KEY"),
    base_url="https://api.notion.com/v1",
    auth_header_format="Bearer {api_key}",
)

notion_secrets = SecretValues(notion, token=os.getenv("NOTION_API_KEY", ""))

slack = Connection(
    name="slack-mcp",
    secrets=SecretKeys(token="SLACK_ACCESS_TOKEN"),
    base_url="https://slack.com/api",
    auth_header_format="Bearer {api_key}",
)

slack_secrets = SecretValues(slack, token=os.getenv("SLACK_ACCESS_TOKEN", ""))

linear = Connection(
    name="linear-mcp",
    secrets=SecretKeys(token="LINEAR_ACCESS_TOKEN"),
    base_url="https://api.linear.app",
    auth_header_format="Bearer {api_key}",
)

linear_secrets = SecretValues(linear, token=os.getenv("LINEAR_API_KEY", ""))
