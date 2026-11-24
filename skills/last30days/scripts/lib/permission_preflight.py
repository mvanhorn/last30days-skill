"""Permission preflight contract and human renderer."""

from __future__ import annotations

import os
import platform
import shutil
from typing import Any
from urllib.parse import urlsplit

from . import corpus, env, providers as provider_endpoints, proxy


ENDPOINT_OVERRIDE_KEYS = {
    "BROWSER_CDP_URL",
    "BSKY_SEARCH_HOST",
    "LAST30DAYS_AMAZON_DOMAIN",
    "LAST30DAYS_API_BASE",
    "LAST30DAYS_SEARXNG_URL",
    "LAST30DAYS_YOUTUBE_SSH_HOST",
    "OPENAI_BASE_URL",
    "OPENROUTER_BASE_URL",
    "SCRAPECREATORS_BASE_URL",
    "XAI_BASE_URL",
    "XIAOHONGSHU_API_BASE",
    "YOUTUBE_PROXY",
    "YT_PROXY",
}

_DEFAULT_AMAZON_DOMAIN = "https://www.amazon.com"

PROVIDER_CREDENTIALS = {
    "google": "Google/Gemini API key",
    "openai": "OpenAI API key",
    "xai": "xAI API key",
    "openrouter": "OpenRouter API key",
    "perplexity": "Perplexity API key",
    "scrapecreators": "ScrapeCreators API key",
    "github": "GitHub token or gh auth",
    # Presence for these keys is computed from config inside build(), never
    # through diagnose.providers, whose key set is frozen by
    # tests/test_diagnose_compat.py.
    "x_bearer": "X API bearer token",
    "x_cookies": "X browser cookies (AUTH_TOKEN/CT0)",
    "brave": "Brave Search API key",
    "exa": "Exa API key",
    "serper": "Serper API key",
    "parallel": "Parallel API key",
    "bluesky": "Bluesky app password",
    "truthsocial": "Truth Social token",
    "apify": "Apify API token",
    "xquik": "Xquik API key",
    "brightdata": "Bright Data API key",
    "groq": "Groq API key",
    "deepline": "Deepline API key",
    "hosted_api": "last30days hosted/proxy API key",
    "publish_password": "HTML publish password",
}


def _has_secret(value: Any) -> bool:
    return bool(str(value or "").strip()) and not proxy.is_sentinel(value)


def _config_or_env(config: dict[str, Any], key: str) -> str:
    raw = config.get(key)
    if raw is None:
        raw = os.environ.get(key)
    return str(raw or "").strip()


def _valid_hosted_api_base(value: str) -> bool:
    if not value or any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch.isspace() for ch in value):
        return False
    if not provider_endpoints.allowed_base_url_override(value):
        return False
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        return False
    if "\\" in parts.netloc:
        return False
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return False
    if parts.query or parts.fragment or "?" in value or "#" in value:
        return False
    return True


def _configured_corpus_dirs(config: dict[str, Any]) -> list[str]:
    """Resolve configured corpus dirs respecting EXCLUDE_SOURCES (matches runtime hosted bypass)."""
    if "corpus" in env.exclude_sources(config):
        return []
    cli_dirs = config.get("_CORPUS_DIRS")
    if isinstance(cli_dirs, str):
        cli_list: list[str] | None = [cli_dirs]
    elif cli_dirs:
        cli_list = [str(item) for item in cli_dirs if str(item).strip()]
    else:
        cli_list = None
    raw = config.get("LAST30DAYS_CORPUS_DIRS")
    if not cli_list and not raw:
        return []
    return [str(path) for path in corpus.resolve_directories(cli_list, raw)]


def _planned_corpus_dirs(
    configured_dirs: list[str],
    requested_sources: list[str] | None = None,
) -> list[str]:
    if requested_sources and "corpus" not in requested_sources:
        return []
    return list(configured_dirs)


def _active_secret_stores(config_source: str) -> list[str]:
    stores: list[str] = []
    if config_source == "keychain":
        stores.append("keychain")
    elif (
        platform.system() == "Darwin"
        and not _truthy(os.environ.get(env.KEYCHAIN_DISABLE_ENV))
        and shutil.which("security")
    ):
        stores.append("keychain")
    if config_source == "pass":
        stores.append("pass")
    elif shutil.which("pass"):
        stores.append("pass")
    return stores


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


def _conditional_x_cookie_routes(config: dict[str, Any]) -> list[str]:
    if (
        not env.x_policy(config).cookie_discovery
        or env.cookie_access_denied(config)
        or (config.get("AUTH_TOKEN") and config.get("CT0"))
    ):
        return []
    from . import agentcookie, chrome_cdp

    routes = []
    if agentcookie.is_available(config):
        routes.append("agentcookie (if available)")
    if chrome_cdp.cookie_access_allowed(config) and any(
        not endpoint.strip().lower().startswith(("https://", "wss://"))
        for endpoint in chrome_cdp.candidate_endpoints(config)
    ):
        routes.append("Chrome CDP (if available)")
    return routes if routes and env.x_extras_enabled(config) else []


def build(
    config: dict[str, Any],
    diagnose: dict[str, Any],
    *,
    planned_save_dir: str | None = None,
    report_on_save_dir: str | None = None,
    requested_sources: list[str] | None = None,
    deep_research: bool = False,
    local_only_run: bool = False,
) -> dict[str, Any]:
    """Build a stable, secret-free permission preflight object."""
    browser = dict(diagnose.get("browser_cookies") or {})
    browser_mode = str(browser.get("mode") or "off")
    browser_browsers = []
    if browser_mode in {"read", "plan_only"}:
        browser_browsers = list(browser.get("browsers") or [])
        if (
            (not requested_sources or "reddit" in requested_sources)
            and "reddit" not in env.exclude_sources(config)
            and not env.reddit_scrapecreators_primary(config)
        ):
            allowed, _ = env.reddit_session_gate({**config, "_BROWSER_COOKIE_MODE": "read"})
            if allowed:
                reddit_browser = str(config[env.REDDIT_FROM_BROWSER_VAR]).strip().lower()
                if reddit_browser not in browser_browsers:
                    browser_browsers.append(reddit_browser)
        browser_browsers.extend(_conditional_x_cookie_routes(config))
    browser_status = "enabled_by_config" if browser_browsers else "off"

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
    if planned_save_dir is not None:
        local_writes = [write for write in local_writes if write.get("kind") != "report"]
        if planned_save_dir:
            local_writes.append({"kind": "report", "path": str(planned_save_dir)})
    local_writes = _dedupe_writes([dict(write) for write in local_writes])
    local_write_paths = {str(write.get("path") or "") for write in local_writes}
    conditional_writes: list[dict[str, str]] = []
    if report_on_save_dir and not planned_save_dir and str(report_on_save_dir) not in local_write_paths:
        conditional_writes.append({"kind": "report_on_save", "path": str(report_on_save_dir)})
    conditional_writes = _dedupe_writes(conditional_writes)

    api_base = _config_or_env(config, "LAST30DAYS_API_BASE")
    raw_hosted_key = config.get("LAST30DAYS_API_KEY")
    if raw_hosted_key is None:
        raw_hosted_key = env.read_secret_env("LAST30DAYS_API_KEY")
    hosted_api_key_present = _has_secret(raw_hosted_key)
    proxy_mode = _config_or_env(config, "LAST30DAYS_API_MODE").lower() == "proxy"
    configured_corpus_dirs = _configured_corpus_dirs(config)
    corpus_dirs = _planned_corpus_dirs(configured_corpus_dirs, requested_sources)
    hosted_base_valid = bool(api_base) and _valid_hosted_api_base(api_base)
    hosted_mode = bool(
        hosted_base_valid
        and hosted_api_key_present
        and not proxy_mode
        and not configured_corpus_dirs
        and not deep_research
        and not local_only_run
    )

    providers = dict(diagnose.get("providers") or {})
    credentials = {
        "google": {
            "present": bool(
                providers.get("google")
                or _has_secret(config.get("GOOGLE_API_KEY"))
                or _has_secret(config.get("GEMINI_API_KEY"))
                or _has_secret(config.get("GOOGLE_GENAI_API_KEY"))
            ),
            "label": PROVIDER_CREDENTIALS["google"],
        },
        "openai": {"present": bool(providers.get("openai")), "label": PROVIDER_CREDENTIALS["openai"]},
        "xai": {
            "present": (
                _has_secret(config.get("XAI_API_KEY"))
                if "XAI_API_KEY" in config
                else bool(providers.get("xai"))
            ),
            "label": PROVIDER_CREDENTIALS["xai"],
        },
        "openrouter": {"present": bool(providers.get("openrouter")), "label": PROVIDER_CREDENTIALS["openrouter"]},
        "perplexity": {"present": bool(providers.get("perplexity")), "label": PROVIDER_CREDENTIALS["perplexity"]},
        "scrapecreators": {
            "present": (
                _has_secret(config.get("SCRAPECREATORS_API_KEY"))
                if "SCRAPECREATORS_API_KEY" in config
                else bool(diagnose.get("has_scrapecreators"))
            ),
            "label": PROVIDER_CREDENTIALS["scrapecreators"],
        },
        "github": {"present": bool(diagnose.get("has_github")), "label": PROVIDER_CREDENTIALS["github"]},
        "x_bearer": {
            "present": _has_secret(config.get("X_BEARER_TOKEN")),
            "label": PROVIDER_CREDENTIALS["x_bearer"],
        },
        "x_cookies": {
            "present": _has_secret(config.get("AUTH_TOKEN")) or _has_secret(config.get("CT0")),
            "label": PROVIDER_CREDENTIALS["x_cookies"],
        },
        "brave": {"present": _has_secret(config.get("BRAVE_API_KEY")), "label": PROVIDER_CREDENTIALS["brave"]},
        "exa": {"present": _has_secret(config.get("EXA_API_KEY")), "label": PROVIDER_CREDENTIALS["exa"]},
        "serper": {"present": _has_secret(config.get("SERPER_API_KEY")), "label": PROVIDER_CREDENTIALS["serper"]},
        "parallel": {"present": _has_secret(config.get("PARALLEL_API_KEY")), "label": PROVIDER_CREDENTIALS["parallel"]},
        "bluesky": {
            "present": _has_secret(config.get("BSKY_APP_PASSWORD")),
            "label": PROVIDER_CREDENTIALS["bluesky"],
        },
        "truthsocial": {
            "present": _has_secret(config.get("TRUTHSOCIAL_TOKEN")),
            "label": PROVIDER_CREDENTIALS["truthsocial"],
        },
        "apify": {"present": _has_secret(config.get("APIFY_API_TOKEN")), "label": PROVIDER_CREDENTIALS["apify"]},
        "xquik": {"present": _has_secret(config.get("XQUIK_API_KEY")), "label": PROVIDER_CREDENTIALS["xquik"]},
        "brightdata": {
            "present": _has_secret(config.get("BRIGHTDATA_API_KEY")),
            "label": PROVIDER_CREDENTIALS["brightdata"],
        },
        "groq": {"present": _has_secret(config.get("GROQ_API_KEY")), "label": PROVIDER_CREDENTIALS["groq"]},
        "deepline": {
            "present": _has_secret(config.get("DEEPLINE_API_KEY")),
            "label": PROVIDER_CREDENTIALS["deepline"],
        },
        "hosted_api": {"present": hosted_api_key_present, "label": PROVIDER_CREDENTIALS["hosted_api"]},
        "publish_password": {
            "present": _has_secret(config.get("LAST30DAYS_PUBLISH_PASSWORD")),
            "label": PROVIDER_CREDENTIALS["publish_password"],
        },
    }

    active_endpoint_overrides: list[str] = []
    rejected_provider_overrides: list[str] = []
    for key in sorted(ENDPOINT_OVERRIDE_KEYS):
        if key == "LAST30DAYS_API_BASE":
            value = api_base
            if not value:
                continue
            if not hosted_base_valid:
                rejected_provider_overrides.append(key)
            else:
                active_endpoint_overrides.append(key)
            continue
        value = str(config.get(key) or "").strip()
        if not value:
            continue
        if key == "LAST30DAYS_AMAZON_DOMAIN" and value.rstrip("/").lower() == _DEFAULT_AMAZON_DOMAIN:
            continue
        if (
            key in provider_endpoints.PROVIDER_BASE_URL_KEYS
            and not provider_endpoints.allowed_base_url_override(value)
        ):
            rejected_provider_overrides.append(key)
        else:
            active_endpoint_overrides.append(key)
    ignored_endpoint_overrides = sorted(
        set(diagnose.get("ignored_endpoint_overrides") or []) | set(rejected_provider_overrides)
    )
    external_commands = {
        name: {"status": _status(bool(available))}
        for name, available in sorted((diagnose.get("external_commands") or {}).items())
    }

    action_items: list[str] = []
    if ignored_project_config:
        action_items.append("Project config was ignored; set LAST30DAYS_TRUST_PROJECT_CONFIG=1 to trust it.")
    # get_config() already emptied these, so the provider flags above read them
    # as absent. Name them anyway: the user's setup is broken in a way the
    # presence booleans alone describe as "nothing configured".
    templated_keys = env.templated_config_keys(config)
    if templated_keys:
        action_items.append(
            "Unsubstituted config template(s) count as unset: "
            + _format_names(templated_keys)
            + ". Replace each with a real value or remove it."
        )
    if rejected_provider_overrides:
        action_items.append(
            "Provider endpoint override(s) ignored: "
            + _format_names(rejected_provider_overrides)
            + ". Use HTTPS, or HTTP on loopback."
        )

    secret_stores = _active_secret_stores(config_source)
    available_sources = ["hosted"] if hosted_mode else list(diagnose.get("available_sources") or [])

    return {
        "status": "action_needed" if action_items else "ready",
        "safe": bool(diagnose.get("safe")),
        "local_reads": {
            "config_source": config_source,
            "secret_stores": secret_stores,
            "corpus_dirs": corpus_dirs,
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
                "reads_values": bool(browser.get("reads_values", False)),
            },
        },
        "local_writes": local_writes,
        "conditional_writes": conditional_writes,
        "external_commands": external_commands,
        "credentials": credentials,
        "network": {
            "available_sources": available_sources,
            "hosted_mode": hosted_mode,
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
    secret_stores = list(reads.get("secret_stores") or [])
    if secret_stores:
        lines.append("- Secret stores: " + _format_names(secret_stores))
    corpus_dirs = list(reads.get("corpus_dirs") or [])
    if corpus_dirs:
        lines.append("- Corpus directories: " + _format_names(corpus_dirs))
    if project.get("status") == "ignored_untrusted":
        ignored_keys = _format_names(list(project.get("ignored_keys") or []))
        lines.append(f"- Project config: ignored untrusted file ({ignored_keys})")
    elif project.get("status") == "trusted_active":
        lines.append("- Project config: trusted and active")
    else:
        lines.append("- Project config: not active")
    if browser.get("status") == "enabled_by_config":
        read_note = (
            "; cookie values were read"
            if browser.get("reads_values")
            else "; preflight did not read cookie values"
        )
        lines.append(
            "- Browser cookies: enabled by config for "
            + _format_names(list(browser.get("browsers") or []))
            + read_note
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
        kind = write.get("kind", "file")
        if kind == "engine_cache_on_first_use":
            lines.append(f"- MCP engine cache (first use may initialize it): {write.get('path')}")
        elif kind.endswith("_on_save"):
            label = kind.removesuffix("_on_save").replace("_", " ").capitalize()
            lines.append(f"- {label} (if saved): {write.get('path')}")
        else:
            lines.append(f"- {kind} (conditional): {write.get('path')}")

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
    if network.get("hosted_mode"):
        lines.append("- Hosted mode: active (LAST30DAYS_API_BASE)")
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
