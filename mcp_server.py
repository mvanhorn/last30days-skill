#!/usr/bin/env python3
"""
MCP Server for last30days skill.

This server exposes the last30days research functionality as an MCP tool
that can be used by Claude Desktop and other MCP-compatible clients.

Usage:
    python3 mcp_server.py

Configure in Claude Desktop's claude_desktop_config.json:
{
    "mcpServers": {
        "last30days": {
            "command": "python3",
            "args": ["/path/to/last30days-skill/mcp_server.py"]
        }
    }
}
"""

import json
import sys
import os
from pathlib import Path

# Get the directory where this script is located (works even when called from different CWD)
SCRIPT_FILE = Path(__file__).resolve()
ROOT_DIR = SCRIPT_FILE.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"

# Add scripts to path for imports
sys.path.insert(0, str(SCRIPTS_DIR))

# Change to the root directory so relative paths work
os.chdir(ROOT_DIR)

try:
    from lib import dates, dedupe, env, http, models, normalize, openai_reddit, openrouter, reddit_enrich, render, schema, score, xai_x
except ImportError as e:
    # Write error to stderr for debugging
    sys.stderr.write(f"[last30days-mcp] Import error: {e}\n")
    sys.stderr.write(f"[last30days-mcp] Script location: {SCRIPT_FILE}\n")
    sys.stderr.write(f"[last30days-mcp] Scripts dir: {SCRIPTS_DIR}\n")
    sys.stderr.write(f"[last30days-mcp] Scripts dir exists: {SCRIPTS_DIR.exists()}\n")
    sys.stderr.write(f"[last30days-mcp] sys.path: {sys.path[:3]}\n")
    sys.stderr.flush()
    raise


def log(msg: str):
    """Log to stderr for debugging."""
    sys.stderr.write(f"[last30days-mcp] {msg}\n")
    sys.stderr.flush()


def read_message():
    """Read a JSON-RPC message from stdin."""
    # Read Content-Length header
    headers = {}
    while True:
        line = sys.stdin.readline()
        if line == '\r\n' or line == '\n' or line == '':
            break
        if ':' in line:
            key, value = line.split(':', 1)
            headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get('content-length', 0))
    if content_length == 0:
        return None

    content = sys.stdin.read(content_length)
    return json.loads(content)


def send_message(msg: dict):
    """Send a JSON-RPC message to stdout."""
    content = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(content)}\r\n\r\n{content}")
    sys.stdout.flush()


def send_result(id, result):
    """Send a successful result."""
    send_message({
        "jsonrpc": "2.0",
        "id": id,
        "result": result
    })


def send_error(id, code, message):
    """Send an error response."""
    send_message({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": code,
            "message": message
        }
    })


def do_research(topic: str, depth: str = "default", sources: str = "auto") -> dict:
    """Execute the research and return results."""
    config = env.get_config()

    # Check available sources
    available = env.get_available_sources(config)
    effective_sources, error = env.validate_sources(sources, available)

    if error and "WebSearch fallback" not in error:
        return {"error": error, "results": None}

    # Get date range (last 30 days)
    from_date, to_date = dates.get_date_range(30)

    # Determine if using OpenRouter
    use_openrouter = env.should_use_openrouter(config)

    # Select models
    selected_models = models.get_models(config)

    # Run searches
    reddit_items = []
    x_items = []
    reddit_error = None
    x_error = None

    run_reddit = effective_sources in ("both", "reddit", "all", "reddit-web")
    run_x = effective_sources in ("both", "x", "all", "x-web")

    # Reddit search
    if run_reddit:
        try:
            if use_openrouter:
                raw = openrouter.search_reddit(
                    config["OPENROUTER_API_KEY"],
                    selected_models.get("openai", "gpt-5.2"),
                    topic, from_date, to_date, depth=depth
                )
                reddit_items = openrouter.parse_reddit_response(raw)
            elif config.get("OPENAI_API_KEY"):
                raw = openai_reddit.search_reddit(
                    config["OPENAI_API_KEY"],
                    selected_models.get("openai", "gpt-5.2"),
                    topic, from_date, to_date, depth=depth
                )
                reddit_items = openai_reddit.parse_reddit_response(raw)
        except Exception as e:
            reddit_error = str(e)

    # X search
    if run_x:
        try:
            if use_openrouter:
                raw = openrouter.search_x(
                    config["OPENROUTER_API_KEY"],
                    selected_models.get("xai", "grok-4-1-fast"),
                    topic, from_date, to_date, depth=depth
                )
                x_items = openrouter.parse_x_response(raw)
            elif config.get("XAI_API_KEY"):
                raw = xai_x.search_x(
                    config["XAI_API_KEY"],
                    selected_models.get("xai", "grok-4-1-fast"),
                    topic, from_date, to_date, depth=depth
                )
                x_items = xai_x.parse_x_response(raw)
        except Exception as e:
            x_error = str(e)

    # Enrich Reddit items
    for i, item in enumerate(reddit_items):
        try:
            reddit_items[i] = reddit_enrich.enrich_reddit_item(item)
        except Exception:
            pass

    # Normalize and filter
    normalized_reddit = normalize.normalize_reddit_items(reddit_items, from_date, to_date)
    normalized_x = normalize.normalize_x_items(x_items, from_date, to_date)

    filtered_reddit = normalize.filter_by_date_range(normalized_reddit, from_date, to_date)
    filtered_x = normalize.filter_by_date_range(normalized_x, from_date, to_date)

    # Score and sort
    scored_reddit = score.score_reddit_items(filtered_reddit)
    scored_x = score.score_x_items(filtered_x)

    sorted_reddit = score.sort_items(scored_reddit)
    sorted_x = score.sort_items(scored_x)

    # Dedupe
    deduped_reddit = dedupe.dedupe_reddit(sorted_reddit)
    deduped_x = dedupe.dedupe_x(sorted_x)

    # Determine mode
    if effective_sources == "both":
        mode = "both"
    elif effective_sources == "reddit":
        mode = "reddit-only"
    elif effective_sources == "x":
        mode = "x-only"
    else:
        mode = effective_sources

    # Create report
    report = schema.create_report(
        topic, from_date, to_date, mode,
        selected_models.get("openai"),
        selected_models.get("xai")
    )
    report.reddit = deduped_reddit
    report.x = deduped_x
    report.reddit_error = reddit_error
    report.x_error = x_error

    # Generate markdown output
    report.context_snippet_md = render.render_context_snippet(report)

    return {
        "topic": topic,
        "date_range": f"{from_date} to {to_date}",
        "reddit_count": len(deduped_reddit),
        "x_count": len(deduped_x),
        "reddit_error": reddit_error,
        "x_error": x_error,
        "markdown": render.render_compact(report),
        "using_openrouter": use_openrouter
    }


def handle_initialize(id, params):
    """Handle initialize request."""
    send_result(id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {}
        },
        "serverInfo": {
            "name": "last30days",
            "version": "1.0.0"
        }
    })


def handle_tools_list(id):
    """Handle tools/list request."""
    send_result(id, {
        "tools": [
            {
                "name": "research_last30days",
                "description": "Research a topic across Reddit and X (Twitter) from the last 30 days. Returns community discussions, engagement metrics, and synthesized insights. Use this when the user wants to know what people are saying about a topic recently.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "The topic to research (e.g., 'Claude Code', 'AI agents', 'React Server Components')"
                        },
                        "depth": {
                            "type": "string",
                            "enum": ["quick", "default", "deep"],
                            "description": "Research depth: 'quick' (8-12 sources), 'default' (20-30 sources), 'deep' (40-60 sources)",
                            "default": "default"
                        },
                        "sources": {
                            "type": "string",
                            "enum": ["auto", "reddit", "x", "both"],
                            "description": "Which sources to search: 'auto' uses available API keys, 'reddit' only, 'x' only, or 'both'",
                            "default": "auto"
                        }
                    },
                    "required": ["topic"]
                }
            }
        ]
    })


def handle_tools_call(id, params):
    """Handle tools/call request."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    if tool_name != "research_last30days":
        send_error(id, -32601, f"Unknown tool: {tool_name}")
        return

    topic = arguments.get("topic")
    if not topic:
        send_error(id, -32602, "Missing required parameter: topic")
        return

    depth = arguments.get("depth", "default")
    sources = arguments.get("sources", "auto")

    try:
        result = do_research(topic, depth, sources)

        # Format response
        content = []

        if result.get("error"):
            content.append({
                "type": "text",
                "text": f"Error: {result['error']}"
            })
        else:
            summary = f"## Research Results: {result['topic']}\n\n"
            summary += f"**Date Range:** {result['date_range']}\n"
            summary += f"**Sources Found:** {result['reddit_count']} Reddit threads, {result['x_count']} X posts\n"

            if result.get("using_openrouter"):
                summary += f"**API:** OpenRouter\n"

            if result.get("reddit_error"):
                summary += f"\n**Reddit Error:** {result['reddit_error']}\n"
            if result.get("x_error"):
                summary += f"\n**X Error:** {result['x_error']}\n"

            summary += f"\n---\n\n{result.get('markdown', 'No results found.')}"

            content.append({
                "type": "text",
                "text": summary
            })

        send_result(id, {"content": content})

    except Exception as e:
        log(f"Error in research: {e}")
        send_error(id, -32603, f"Research failed: {str(e)}")


def main():
    """Main MCP server loop."""
    log("Starting last30days MCP server...")
    log(f"Root directory: {ROOT_DIR}")
    log(f"Scripts directory: {SCRIPTS_DIR}")

    while True:
        try:
            message = read_message()
            if message is None:
                log("No message received, exiting")
                break

            method = message.get("method")
            id = message.get("id")
            params = message.get("params", {})

            log(f"Received: {method}")

            if method == "initialize":
                handle_initialize(id, params)
            elif method == "notifications/initialized":
                # Client acknowledged initialization
                log("Client initialized")
            elif method == "tools/list":
                handle_tools_list(id)
            elif method == "tools/call":
                handle_tools_call(id, params)
            elif method == "shutdown":
                send_result(id, None)
                log("Shutdown requested")
                break
            else:
                if id is not None:
                    send_error(id, -32601, f"Method not found: {method}")

        except KeyboardInterrupt:
            log("Interrupted")
            break
        except Exception as e:
            import traceback
            log(f"Error: {e}")
            log(f"Traceback: {traceback.format_exc()}")
            if 'id' in dir() and id is not None:
                send_error(id, -32603, str(e))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        sys.stderr.write(f"[last30days-mcp] Fatal error: {e}\n")
        sys.stderr.write(f"[last30days-mcp] {traceback.format_exc()}\n")
        sys.stderr.flush()
        sys.exit(1)
