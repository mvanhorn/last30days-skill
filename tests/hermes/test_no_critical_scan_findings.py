"""Regression guard: the Hermes install-time scanner must find zero CRITICAL.

Hermes (NousResearch/hermes-agent, tools/skills_guard.py) blocks community-tier
installs on a `dangerous` verdict, which any single CRITICAL finding produces,
and `--force` cannot override it. With zero CRITICAL the verdict is `caution`,
which `hermes skills install ... --force` installs. Issues #513 and #1502 were
both CRITICAL findings sneaking back in; this test fails CI instead of silently
re-blocking every Hermes user.

Self-contained replica (no Hermes dependency): every CRITICAL rule from
skills_guard.py's THREAT_PATTERNS, copied verbatim from scanner version
`skills-guard-v16` (hermes-agent 27e38935). The real scanner compiles them with
re.IGNORECASE and checks each line. It also demotes some hits by context; this
replica does not, so it is stricter than Hermes, never looser.

Scanner v16 no longer honors a skill's own `.skillignore` (publisher-authored
ignore files are untrusted), so the whole skill directory is scanned, vendored
code included. HIGH/MEDIUM findings are intentionally not checked: they only
produce `caution`, which is installable.

To resync after Hermes changes its rules, regenerate CRITICAL_RULES from
`[p for p in THREAT_PATTERNS if p[2] == "critical"]` and update the version
above.
"""
from __future__ import annotations

import re
from pathlib import Path

# scan root == the skill directory (where SKILL.md lives), matching how Hermes
# resolves owner/repo/skills/<name> and raw SKILL.md URLs.
SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "last30days"

# Every CRITICAL rule in skills-guard-v16 THREAT_PATTERNS, verbatim.
CRITICAL_RULES = [
    ('curl\\s+(?![^\\n]*https?://(?:localhost|127\\.0\\.0\\.1|\\[::1\\]))[^\\n]*\\$\\{?\\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)S?\\b', 'env_exfil_curl'),
    ('wget\\s+(?![^\\n]*https?://(?:localhost|127\\.0\\.0\\.1|\\[::1\\]))[^\\n]*\\$\\{?\\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)S?\\b', 'env_exfil_wget'),
    ('fetch\\s*\\((?![^\\n]*https?://(?:localhost|127\\.0\\.0\\.1|\\[::1\\]))[^\\n]*\\$\\{?\\w*(?:KEY|TOKEN|SECRET|PASSWORD)S?\\b', 'env_exfil_fetch'),
    ('httpx?\\.(get|post|put|patch)\\s*\\((?![^\\n]*https?://(?:localhost|127\\.0\\.0\\.1|\\[::1\\]))[^\\n]*(KEY|TOKEN|SECRET|PASSWORD)', 'env_exfil_httpx'),
    ('requests\\.(get|post|put|patch)\\s*\\((?![^\\n]*https?://(?:localhost|127\\.0\\.0\\.1|\\[::1\\]))[^\\n]*(KEY|TOKEN|SECRET|PASSWORD)', 'env_exfil_requests'),
    ('\\$HOME/\\.hermes/\\.env|\\~/\\.hermes/\\.env', 'hermes_env_access'),
    ('cat\\s+(?!>)[^\\n]*(\\.env|credentials|\\.netrc|\\.pgpass|\\.npmrc|\\.pypirc)', 'read_secrets_file'),
    ('\\b(?:readFile(?:Sync)?|readTextFile)\\s*\\(\\s*["\\\'][^"\\\'\\n]*(?:\\.ssh[/\\\\]id_(?:rsa|ed25519|ecdsa|dsa)(?!\\.pub)|\\.env\\b|credentials\\b|\\.netrc\\b|\\.pgpass\\b|\\.npmrc\\b|\\.pypirc\\b)[^"\\\'\\n]*["\\\']', 'js_read_secrets_file'),
    ('\\bopen\\s*\\(\\s*(?:os\\.path\\.expanduser\\s*\\(\\s*)?["\\\'][^"\\\'\\n]*(?:\\.ssh[/\\\\]id_(?:rsa|ed25519|ecdsa|dsa)(?!\\.pub)|\\.env\\b|credentials\\b|\\.netrc\\b|\\.pgpass\\b|\\.npmrc\\b|\\.pypirc\\b)[^"\\\'\\n]*["\\\']\\s*\\)?(?!\\s*\\)?\\s*,\\s*["\\\'][^"\\\']*[wax][^"\\\']*["\\\'])(?![^\\n]*\\bmode\\s*=\\s*["\\\'][^"\\\']*[wax])|\\bPath\\s*\\(\\s*(?:os\\.path\\.expanduser\\s*\\(\\s*)?["\\\'][^"\\\'\\n]*(?:\\.ssh[/\\\\]id_(?:rsa|ed25519|ecdsa|dsa)(?!\\.pub)|\\.env\\b|credentials\\b|\\.netrc\\b|\\.pgpass\\b|\\.npmrc\\b|\\.pypirc\\b)[^"\\\'\\n]*["\\\']\\s*\\)?\\s*\\)\\.(?:read_text|read_bytes|readlines|readline)\\s*\\(', 'py_read_secrets_file'),
    ('(?-i:ENV)\\[.*(?:KEY|TOKEN|SECRET|PASSWORD)', 'ruby_env_secret'),
    ('(?<![-/])\\b(?:dig|nslookup)\\s+(?:[-+@]\\S*(?:\\s+[^\\s$"\\\'-][^\\s$]*)?\\s+)*["\\\']?[^\\s"\\\'$()]*\\$(?:\\{[^}\\s]*\\}|\\([^\\n]*?\\)+|\\w+)[\\w-]*["\\\']?\\.[a-z]|(?<![-/])\\bhost\\s+(?:[-+@]\\S*(?:\\s+[^\\s$"\\\'-][^\\s$]*)?\\s+)*["\\\']?[^\\s"\\\'$()]*\\$(?:\\{[^}\\s]*\\}|\\([^\\n]*?\\)+|\\w+)[\\w-]*\\.[a-z]|(?:^|[;&|]|\\$\\(|\\b(?:do|then|else)\\b)\\s*host\\s+(?:[-+@]\\S*(?:\\s+[^\\s$"\\\'-][^\\s$]*)?\\s+)*["\\\']?\\$', 'dns_exfil'),
    ('>\\s*/tmp/[^\\s]*\\s*&&\\s*(curl|wget|nc|python)', 'tmp_staging'),
    ('ignore\\s+(?:\\w+\\s+)*(previous|all|above|prior)\\s+instructions', 'prompt_injection_ignore'),
    ('system\\s+(?:\\w+\\s+)*prompt\\s+(?:\\w+\\s+)*override', 'sys_prompt_override'),
    ('disregard\\s+(?:\\w+\\s+)*(your|all|any)\\s+(?:\\w+\\s+)*(instructions|rules|guidelines)', 'disregard_rules'),
    ("act\\s+as\\s+(if|though)\\s+(?:\\w+\\s+)*you\\s+(?:\\w+\\s+)*(have\\s+no|don\\'t\\s+have)\\s+(?:\\w+\\s+)*(restrictions|limits|rules)", 'bypass_restrictions'),
    ('translate\\s+.*\\s+into\\s+.*\\s+and\\s+(execute|run|eval)', 'translate_execute'),
    ('rm\\s+-rf\\s+((?:[^\\s;&|<>#][^\\s;&|<>]*\\s+)+?)?/(?(1)(?!/(?:\\s|$)))(?:(?!(?:tmp|var/tmp|dev/shm|run|var/lib/apt/lists|var/cache/(?:apt|apk|yum|dnf))(?:\\b|/))|(?:tmp|var/tmp|dev/shm|run|var/lib/apt/lists|var/cache/(?:apt|apk|yum|dnf))/(?:[^/\\s]*/)*\\.\\.(?=/|[\\s;&|]|$))', 'destructive_root_rm'),
    ('(?<![A-Za-z0-9])rm\\s+(-[^\\s]*)?r.*(?:\\$HOME|~[/\\s*]|~$)|(?<![A-Za-z0-9])rmdir\\s+.*(?:\\$HOME|~[/\\s*]|~$)', 'destructive_home_rm'),
    ('>\\s*/etc/', 'system_overwrite'),
    ('\\bmkfs\\b', 'format_filesystem'),
    ('\\bdd\\s+.*if=.*of=/dev/', 'disk_overwrite'),
    ('truncate\\s+-s\\s*0\\s+/', 'truncate_system'),
    ('authorized_keys', 'ssh_backdoor'),
    ('/etc/sudoers|visudo', 'sudoers_mod'),
    ('\\bnc\\s+-[lp]|ncat\\s+-[lp]|\\bsocat\\b[^\\n]*\\b(?:tcp|udp|openssl|ssl|exec|system|pty|unix)[\\w-]*:', 'reverse_shell'),
    ('/bin/(?:bash|sh|zsh|ksh|dash)\\b\\s+-i\\s+.*>/dev/tcp/', 'bash_reverse_shell'),
    ('python[23]?\\s+-c\\s+["\\\']import\\s+socket', 'python_socket_oneliner'),
    ('echo\\s+[^\\n]*\\|\\s*(?:(?:bash|sh|zsh|ksh|dash)\\b|python|perl|ruby|node)', 'echo_pipe_exec'),
    ('/etc/passwd|/etc/shadow', 'system_passwd_access'),
    ('xmrig|stratum\\+tcp|monero|coinhive|cryptonight', 'crypto_mining'),
    ('curl\\s+[^|\\s][^\\n]*\\|\\s*(?:sudo\\s+(?:-\\S+\\s+)*)?(?:bash|sh|zsh|ksh|dash)\\b', 'curl_pipe_shell'),
    ('wget\\s+[^\\n]*-O\\s*-\\s*\\|\\s*(?:sudo\\s+(?:-\\S+\\s+)*)?(?:bash|sh|zsh|ksh|dash)\\b', 'wget_pipe_shell'),
    ('curl\\s+[^|\\s][^\\n]*\\|\\s*(?:sudo\\s+(?:-\\S+\\s+)*)?python', 'curl_pipe_python'),
    ('(?<![a-z])set(?:e|re|res)?[ug]id\\s*\\(|\\bcap_set[ug]id\\b|\\bchmod\\s+(?:-\\w+\\s+)*(?:[ugoa]*[+=][rwxXt]*s|0?[2-7][0-7]{3}\\b)', 'setuid_setgid'),
    ('NOPASSWD', 'nopasswd_sudo'),
    ('^\\s*(?:[-*+]\\s+|\\d+[.)]\\s+)?(?:\\bwrit(?:e|es|ing)\\b|\\bwritten\\b|\\bedit(?:s|ed|ing)?\\b|\\bmodif(?:y|ies|ied|ying|ication)s?\\b|\\bupdat(?:e|es|ed|ing)\\b|\\bappend(?:s|ed|ing)?\\b|\\bprepend(?:s|ed|ing)?\\b|\\binject(?:s|ed|ing)?\\b|\\boverwrit(?:e|es|ing)\\b|\\boverwritten\\b|\\breplac(?:e|es|ed|ing)\\b|\\balter(?:s|ed|ing)?\\b|\\badd(?:s|ed|ing)\\b)[^\\n,]{0,80}?(?:AGENTS\\.md|CLAUDE\\.md|\\.cursorrules|\\.clinerules)\\b|(?:\\byou\\s+(?:must|should|need\\s+to)\\s+|\\bplease\\s+|\\bmake\\s+sure\\s+(?:to\\s+|you\\s+)|\\bbe\\s+sure\\s+to\\s+)(?:\\bwrit(?:e|es|ing)\\b|\\bwritten\\b|\\bedit(?:s|ed|ing)?\\b|\\bmodif(?:y|ies|ied|ying|ication)s?\\b|\\bupdat(?:e|es|ed|ing)\\b|\\bappend(?:s|ed|ing)?\\b|\\bprepend(?:s|ed|ing)?\\b|\\binject(?:s|ed|ing)?\\b|\\boverwrit(?:e|es|ing)\\b|\\boverwritten\\b|\\breplac(?:e|es|ed|ing)\\b|\\balter(?:s|ed|ing)?\\b|\\badd(?:s|ed|ing)\\b)[^\\n,]{0,80}?(?:AGENTS\\.md|CLAUDE\\.md|\\.cursorrules|\\.clinerules)\\b', 'agent_config_mod'),
    ('(?:>>|[\\w"\\\'`)\\]]\\s*>)\\s*[~\\w./-]*(?:AGENTS\\.md|CLAUDE\\.md|\\.cursorrules|\\.clinerules)(?!\\.?\\w)|\\bsed\\b[^\\n]*\\s(?:-[A-Za-z]*i[A-Za-z]*|--in-place)\\b[^\\n]*(?:AGENTS\\.md|CLAUDE\\.md|\\.cursorrules|\\.clinerules)(?!\\.?\\w)|\\btee\\s+(?:-a\\s+)?[~\\w./"\\\'-]*(?:AGENTS\\.md|CLAUDE\\.md|\\.cursorrules|\\.clinerules)(?!\\.?\\w)|\\b(?:cp|mv)\\s+[^\\s|;&]+\\s+[^\\n|;&]{0,40}?(?:AGENTS\\.md|CLAUDE\\.md|\\.cursorrules|\\.clinerules)(?!\\.?\\w)', 'agent_config_mod_shell'),
    ('(?:>>|[\\w"\\\'`)\\]]\\s*>)\\s*[~\\w./-]*\\.hermes/(?:config\\.yaml|SOUL\\.md)(?!\\.?\\w)|\\bsed\\b[^\\n]*\\s(?:-[A-Za-z]*i[A-Za-z]*|--in-place)\\b[^\\n]*\\.hermes/(?:config\\.yaml|SOUL\\.md)(?!\\.?\\w)|\\btee\\s+(?:-a\\s+)?[~\\w./"\\\'-]*\\.hermes/(?:config\\.yaml|SOUL\\.md)(?!\\.?\\w)|\\b(?:cp|mv)\\s+[^\\s|;&]+\\s+[^\\n|;&]{0,40}?\\.hermes/(?:config\\.yaml|SOUL\\.md)(?!\\.?\\w)', 'hermes_config_mod_shell'),
    ('(?:>>|[\\w"\\\'`)\\]]\\s*>)\\s*[~\\w./-]*\\.(?:claude/settings|codex/config)[\\w.]*(?!\\.?\\w)|\\bsed\\b[^\\n]*\\s(?:-[A-Za-z]*i[A-Za-z]*|--in-place)\\b[^\\n]*\\.(?:claude/settings|codex/config)[\\w.]*(?!\\.?\\w)|\\btee\\s+(?:-a\\s+)?[~\\w./"\\\'-]*\\.(?:claude/settings|codex/config)[\\w.]*(?!\\.?\\w)|\\b(?:cp|mv)\\s+[^\\s|;&]+\\s+[^\\n|;&]{0,40}?\\.(?:claude/settings|codex/config)[\\w.]*(?!\\.?\\w)', 'other_agent_config_mod_shell'),
    ('(?:api[_-]?key|token|secret|password)\\s*[=:]\\s*["\\\'](?!(?-i:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)["\\\'])(?!(?-i:TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA|TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb|ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL|MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr|11111111111111111111111111111111|metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s)["\\\'])[A-Za-z0-9+/=_-]{20,}', 'hardcoded_secret'),
    ('(?-i:-----BEGIN\\s+(RSA\\s+)?PRIVATE\\s+KEY-----)', 'embedded_private_key'),
    ('(?-i:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{80,})', 'github_token_leaked'),
    ('(?-i:sk-[A-Za-z0-9]{20,})', 'openai_key_leaked'),
    ('(?-i:sk-ant-[A-Za-z0-9_-]{90,})', 'anthropic_key_leaked'),
    ('(?-i:AKIA[0-9A-Z]{16})', 'aws_access_key_leaked'),
    ('(?-i:glpat-[A-Za-z0-9_\\-]{20,})', 'gitlab_token_leaked'),
    ('\\bDAN\\s+mode\\b|Do\\s+Anything\\s+Now', 'jailbreak_dan'),
    ('\\bdeveloper\\s+mode\\b.*\\benabled?\\b', 'jailbreak_dev_mode'),
    ('(respond|answer|reply)\\s+without\\s+(?:\\w+\\s+)*(restrictions|limitations|filters|safety)', 'remove_filters'),
]

# Binary/asset extensions the scanner does not run text rules on.
_SKIP_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".mp3", ".json"}


def _scanned_files():
    for p in sorted(SKILL_ROOT.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix.lower() in _SKIP_SUFFIXES or p.suffix.lower() == ".pyc":
            continue
        yield p.relative_to(SKILL_ROOT).as_posix(), p


def _critical_hits(rules=CRITICAL_RULES):
    compiled = [(re.compile(rx, re.IGNORECASE), name) for rx, name in rules]
    hits = []
    for rel, path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for rx, name in compiled:
                if rx.search(line):
                    hits.append(f"{name}  {rel}:{i}  {line.strip()[:90]}")
    return hits


def test_zero_critical_scanner_findings():
    hits = _critical_hits()
    assert not hits, (
        "Hermes scanner CRITICAL patterns reappeared in the scanned subtree "
        "(this re-blocks every community install). Findings:\n  " + "\n  ".join(hits)
    )


def test_replica_catches_download_and_execute_install_hint():
    """The rule that caused #1502 must stay in the replica."""
    rx = dict((name, rx) for rx, name in CRITICAL_RULES)["curl_pipe_shell"]
    assert re.search(rx, "install with curl -fsSL https://example.com/install.sh | bash", re.IGNORECASE)
    assert not re.search(rx, "npm install -g @xai-official/grok", re.IGNORECASE)
