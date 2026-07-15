"""Permission preflight contract and human renderer."""

from __future__ import annotations

from typing import Any


ENDPOINT_OVERRIDE_KEYS = {
    "BSKY_SEARCH_HOST",
    "LAST30DAYS_SEARXNG_URL",
    "LAST30DAYS_YOUTUBE_SSH_HOST",
    "OPENAI_BASE_URL",
    "XAI_BASE_URL",
    "XIAOHONGSHU_API_BASE",
}

PROVIDER_CREDENTIALS = {
    "google": "Google/Gemini API key",
    "openai": "OpenAI API key",
    "xai": "xAI API key",
    "openrouter": "OpenRouter API key",
    "perplexity": "Perplexity API key",
    "scrapecreators": "ScrapeCreators API key",
    "github": "GitHub token or gh auth",
}


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _status(value: bool) -> str:
    return "available" if value else "unavailable"


def _write_key(write: dict[str, str]) -> tuple[str, str]:
    return str(write.get("kind") or ""), str(write.get("path") or "")


def _dedupe_writes(writes: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for write in writes:
        key = _write_key(write)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(write)
    return deduped


def build(
    config: dict[str, Any],
    diagnose: dict[str, Any],
    *,
    planned_save_dir: str | None = None,
    report_on_save_dir: str | None = None,
) -> dict[str, Any]:
    """Build a stable, secret-free permission preflight object."""
    browser = dict(diagnose.get("browser_cookies") or {})
    browser_mode = str(browser.get("mode") or "off")
    browser_browsers = list(browser.get("browsers") or [])
    browser_enabled = browser_mode in {"read", "plan_only"} and bool(browser_browsers)
    if browser_enabled:
        browser_status = "enabled_by_config"
    else:
        browser_status = "off"

    ignored_project_config = diagnose.get("ignored_project_config")
    config_source = str(diagnose.get("config_source") or "env_only")
    project_config_active = config_source.startswith("project:")
    if project_config_active:
        project_status = "trusted_active"
    elif ignored_project_config:
        project_status = "ignored_untrusted"
    else:
        project_status = "not_active"

    local_writes = list(diagnose.get("local_writes") or [])
    if planned_save_dir:
        local_writes = [{"kind": "report", "path": str(planned_save_dir)}]
    local_writes = _dedupe_writes([dict(write) for write in local_writes])
    local_write_paths = {str(write.get("path") or "") for write in local_writes}
    conditional_writes: list[dict[str, str]] = []
    if report_on_save_dir and not planned_save_dir and str(report_on_save_dir) not in local_write_paths:
        conditional_writes.append({"kind": "report_on_save", "path": str(report_on_save_dir)})
    conditional_writes = _dedupe_writes(conditional_writes)

    providers = dict(diagnose.get("providers") or {})
    credentials = {
        "google": {"present": bool(providers.get("google")), "label": PROVIDER_CREDENTIALS["google"]},
        "openai": {"present": bool(providers.get("openai")), "label": PROVIDER_CREDENTIALS["openai"]},
        "xai": {"present": bool(providers.get("xai")), "label": PROVIDER_CREDENTIALS["xai"]},
        "openrouter": {"present": bool(providers.get("openrouter")), "label": PROVIDER_CREDENTIALS["openrouter"]},
        "perplexity": {"present": bool(providers.get("perplexity")), "label": PROVIDER_CREDENTIALS["perplexity"]},
        "scrapecreators": {
            "present": bool(diagnose.get("has_scrapecreators")),
            "label": PROVIDER_CREDENTIALS["scrapecreators"],
        },
        "github": {"present": bool(diagnose.get("has_github")), "label": PROVIDER_CREDENTIALS["github"]},
    }

    active_endpoint_overrides = sorted(
        key for key in ENDPOINT_OVERRIDE_KEYS if config.get(key)
    )
    ignored_endpoint_overrides = sorted(diagnose.get("ignored_endpoint_overrides") or [])
    external_commands = {
        name: {"status": _status(bool(available))}
        for name, available in sorted((diagnose.get("external_commands") or {}).items())
    }

    action_items: list[str] = []
    if ignored_project_config:
        action_items.append("Project config was ignored; set LAST30DAYS_TRUST_PROJECT_CONFIG=1 to trust it.")

    return {
        "status": "action_needed" if action_items else "ready",
        "safe": bool(diagnose.get("safe")),
        "local_reads": {
            "config_source": config_source,
            "project_config": {
                "status": project_status,
                "trusted": bool(project_config_active),
                "ignored_path": ignored_project_config,
                "ignored_keys": list(diagnose.get("ignored_project_config_keys") or []),
            },
            "browser_cookies": {
                "status": browser_status,
                "mode": browser_mode,
                "browsers": browser_browsers,
                "reads_values": False,
            },
        },
        "local_writes": local_writes,
        "conditional_writes": conditional_writes,
        "external_commands": external_commands,
        "credentials": credentials,
        "network": {
            "available_sources": list(diagnose.get("available_sources") or []),
            "native_search": bool(diagnose.get("native_search")),
            "endpoint_overrides": active_endpoint_overrides,
            "ignored_endpoint_overrides": ignored_endpoint_overrides,
        },
        "action_items": action_items,
    }


def _format_names(names: list[str]) -> str:
    return ", ".join(names) if names else "none"


def render_text(preflight: dict[str, Any]) -> str:
    """Render the permission preflight as concise user-facing text."""
    lines: list[str] = ["last30days preflight"]
    status = preflight.get("status")
    if status == "ready":
        lines.append("Status: Ready to research with safe defaults.")
    else:
        lines.append("Status: Ready, with item(s) to review.")

    reads = preflight.get("local_reads") or {}
    project = reads.get("project_config") or {}
    browser = reads.get("browser_cookies") or {}
    writes = list(preflight.get("local_writes") or [])
    conditional_writes = list(preflight.get("conditional_writes") or [])
    commands = preflight.get("external_commands") or {}
    credentials = preflight.get("credentials") or {}
    network = preflight.get("network") or {}

    lines.append("")
    lines.append("Local reads:")
    lines.append(f"- Config source: {reads.get('config_source') or 'env_only'}")
    if project.get("status") == "ignored_untrusted":
        ignored_keys = _format_names(list(project.get("ignored_keys") or []))
        lines.append(f"- Project config: ignored untrusted file ({ignored_keys})")
    elif project.get("status") == "trusted_active":
        lines.append("- Project config: trusted and active")
    else:
        lines.append("- Project config: not active")
    if browser.get("status") == "enabled_by_config":
        lines.append(
            "- Browser cookies: enabled by config for "
            + _format_names(list(browser.get("browsers") or []))
            + "; preflight did not read cookie values"
        )
    else:
        lines.append("- Browser cookies: off; no browser stores will be read")

    lines.append("")
    lines.append("Local writes:")
    if writes:
        for write in writes:
            lines.append(f"- {write.get('kind', 'file')}: {write.get('path')}")
    else:
        lines.append("- none planned")
    for write in conditional_writes:
        if write.get("kind") == "report_on_save":
            lines.append(f"- Report (if saved): {write.get('path')}")
        else:
            lines.append(f"- {write.get('kind', 'file')} (conditional): {write.get('path')}")

    present_credentials = [
        str(info.get("label") or name)
        for name, info in credentials.items()
        if info.get("present")
    ]
    lines.append("")
    lines.append("Credentials:")
    lines.append("- Present: " + _format_names(present_credentials))
    lines.append("- Values are not printed or written by preflight")

    unavailable_commands = [
        name for name, info in commands.items() if info.get("status") == "unavailable"
    ]
    lines.append("")
    if unavailable_commands:
        lines.append("Optional commands unavailable: " + _format_names(unavailable_commands))
    else:
        lines.append("Optional commands: available")

    endpoint_overrides = list(network.get("endpoint_overrides") or [])
    ignored_endpoint_overrides = list(network.get("ignored_endpoint_overrides") or [])
    lines.append("")
    lines.append("Network:")
    lines.append("- Available sources: " + _format_names(list(network.get("available_sources") or [])))
    if endpoint_overrides:
        lines.append("- Endpoint overrides active: " + _format_names(endpoint_overrides))
    if ignored_endpoint_overrides:
        lines.append("- Endpoint overrides ignored: " + _format_names(ignored_endpoint_overrides))

    action_items = list(preflight.get("action_items") or [])
    lines.append("")
    if action_items:
        lines.append("Next:")
        for item in action_items:
            lines.append(f"- {item}")
    else:
        lines.append("Next: run research normally, or configure optional sources if you need more coverage.")

    return "\n".join(lines) + "\n"
