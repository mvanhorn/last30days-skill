"""NFL entity resolution for nfl30 — every topic is assumed to be about the NFL.

The upstream last30days engine treats every topic as a generic research
subject. nfl30 inverts that the same way marketvalue180 does for equities:
the slash command exists to research a team, a player, a game, or the league,
so the engine tries hard to resolve the topic to one primary entity and keys
the beat-writer, team-official, and NFL-market lanes off it.

Resolution order (first hit wins):
  1. ``explicit_team`` / ``explicit_player`` — the ``--team`` / ``--player``
     flags the model resolved in SKILL.md Step 0.5.
  2. A game: ``"Packers vs Lions"``, ``"Chiefs at Bills"``, ``"KC-BUF"`` — both
     sides must resolve to a team.
  3. An upper-case team abbreviation (``KC``, ``GB``, ``SF``). Abbreviations
     that are also English words (``NO``, ``NE``, ``LA``) only count when the
     topic carries NFL vocabulary or is very short.
  4. A nickname or alias as a whole word (``chiefs``, ``niners``, ``kansas
     city``). Bare cities shared by two teams (``new york``, ``los angeles``)
     never resolve on their own.
  5. A player or coach from ``data/nfl_players.json`` (full name, or a unique
     last name when the topic has NFL vocabulary).
  6. League vocabulary (``nfl``, ``week 3``, ``mvp``, ``trade deadline``) →
     ``kind="league"``.

Everything is static and offline: the team table, the beat-writer roster, and
the player map ship as JSON under ``lib/data/``. Users override the roster at
``~/.config/nfl30/beat_writers.json`` (see ``load_beat_writers``).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import env, log

DATA_DIR = Path(__file__).resolve().parent / "data"
TEAMS_FILE = DATA_DIR / "nfl_teams.json"
BEAT_WRITERS_FILE = DATA_DIR / "nfl_beat_writers.json"
PLAYERS_FILE = DATA_DIR / "nfl_players.json"
OVERRIDE_FILENAME = "beat_writers.json"

# Topic words that mark an NFL context. Used to admit ambiguous matches
# (word-shaped abbreviations, bare cities, bare last names).
NFL_VOCAB = re.compile(
    r"\b(nfl|football|quarterback|qb|wr|rb|te|cb|edge|touchdown|super bowl|"
    r"playoffs?|wild card|injur(?:y|ies|ed)|depth chart|week\s*\d{1,2}|"
    r"preseason|offseason|training camp|draft|roster|coach(?:ing)?|"
    r"head coach|offensive|defensive|coordinator|game|kickoff|snap|practice|"
    r"presser|press conference|postgame|post-game|trade(?:d|s)?|waive[ds]?|"
    r"sign(?:ed|ing|s)?|contract|extension|afc|nfc|division|conference|"
    r"franchise tag|free agen(?:t|cy)|ir\b|questionable|doubtful|"
    r"moneyline|spread|odds|mvp|dpoy|opoy|hall of fame)\b",
    re.IGNORECASE,
)

LEAGUE_WORDS = re.compile(
    r"\b(nfl|super bowl|pro bowl|nfl draft|trade deadline|free agency|"
    r"week\s*\d{1,2}|mvp|dpoy|opoy|oroy|droy|coach of the year|"
    r"power rankings|playoff picture|wild card)\b",
    re.IGNORECASE,
)

# Abbreviations that read as ordinary words; require NFL context or a short topic.
_WORD_SHAPED_ABBRS = frozenset({"NO", "NE", "LA", "IN", "AT", "ON", "OR", "SO"})
_ABBR_TOKEN = re.compile(r"(?<![\w$@#])([A-Z]{2,3})(?![\w])")
_GAME_SPLIT = re.compile(
    r"^\s*(.+?)(?:\s+(?:vs\.?|versus|v\.?|at)\s+|\s*[@\-–—]\s*)(.+?)\s*$", re.IGNORECASE
)

# Cities (or partial names) shared by two franchises; never resolve alone.
_SHARED_CITIES = frozenset({"new york", "los angeles", "la", "ny"})

BEAT_HANDLE_CAPS: dict[str, tuple[int, int]] = {
    # depth: (team handles, national handles)
    "quick": (2, 2),
    "default": (6, 4),
    "deep": (10, 6),
}


def _log(msg: str) -> None:
    log.source_log("NFL", msg, tty_only=False)


@dataclass(frozen=True)
class Team:
    abbr: str
    pm_abbr: str
    city: str
    nickname: str
    name: str
    division: str
    subreddit: str
    x_handle: str
    youtube_handle: str
    domain: str
    rss: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pm_tokens(self) -> tuple[str, ...]:
        """Tokens a Polymarket title uses for this team."""
        return (self.nickname.lower(), self.city.lower(), self.name.lower())

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        return data


@dataclass(frozen=True)
class NflEntity:
    kind: str  # team | player | coach | game | league
    team: Team | None = None
    opponent: Team | None = None
    person: str | None = None
    person_handle: str | None = None
    how: str = "explicit"

    @property
    def label(self) -> str:
        if self.kind == "game" and self.team and self.opponent:
            return f"{self.team.nickname} vs {self.opponent.nickname}"
        if self.kind in {"player", "coach"} and self.person:
            return self.person
        if self.team:
            return self.team.name
        return "NFL"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "team": self.team.as_dict() if self.team else None,
            "opponent": self.opponent.as_dict() if self.opponent else None,
            "person": self.person,
            "person_handle": self.person_handle,
            "how": self.how,
            "label": self.label,
        }


# ---------------------------------------------------------------------------
# Static tables
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_teams() -> dict[str, Team]:
    """Abbreviation → Team for all 32 franchises (module-cached)."""
    raw = _read_json(TEAMS_FILE)
    teams: dict[str, Team] = {}
    for abbr, row in raw["teams"].items():
        teams[abbr] = Team(
            abbr=abbr,
            pm_abbr=str(row.get("pm_abbr") or abbr.lower()),
            city=row["city"],
            nickname=row["nickname"],
            name=row.get("name") or f"{row['city']} {row['nickname']}",
            division=row["division"],
            subreddit=row["subreddit"],
            x_handle=row["x_handle"],
            youtube_handle=row["youtube_handle"],
            domain=row["domain"],
            rss=row.get("rss"),
            aliases=tuple(a.lower() for a in row.get("aliases", [])),
        )
    return teams


@lru_cache(maxsize=1)
def _alias_index() -> list[tuple[str, str]]:
    """(alias, abbr) pairs, longest alias first so 'kansas city chiefs' beats 'chiefs'."""
    pairs: list[tuple[str, str]] = []
    for abbr, team in load_teams().items():
        names = {
            team.nickname.lower(),
            team.name.lower(),
            f"{team.city.lower()} {team.nickname.lower()}",
            *team.aliases,
        }
        if team.city.lower() not in _SHARED_CITIES:
            names.add(team.city.lower())
        for alias in names:
            if alias and alias not in _SHARED_CITIES:
                pairs.append((alias, abbr))
    pairs.sort(key=lambda p: (-len(p[0]), p[0]))
    return pairs


@lru_cache(maxsize=1)
def load_players() -> dict[str, dict[str, Any]]:
    """Lower-cased full name → {team, role, x_handle, name}."""
    raw = _read_json(PLAYERS_FILE)
    players: dict[str, dict[str, Any]] = {}
    for name, row in raw["players"].items():
        players[name.lower()] = {"name": name, **row}
    return players


def _override_path() -> Path:
    return Path(env.CONFIG_DIR) / OVERRIDE_FILENAME


def _builtin_beat_writers() -> dict[str, Any]:
    return _read_json(BEAT_WRITERS_FILE)


def load_beat_writers(override_path: Path | None = None) -> dict[str, Any]:
    """Built-in roster merged with the user's override file.

    Merge rules: a per-team list in the override REPLACES the built-in list
    for that team (a user maintaining a team wants control); ``national`` is
    UNIONED (built-in first, then new handles); ``remove`` lists handles to
    drop everywhere. Malformed overrides are logged and ignored.
    """
    roster = _builtin_beat_writers()
    teams: dict[str, list[dict[str, Any]]] = {
        k: list(v) for k, v in (roster.get("teams") or {}).items()
    }
    national: list[dict[str, Any]] = list(roster.get("national") or [])
    path = override_path or _override_path()
    if path.exists():
        try:
            override = _read_json(path)
            if not isinstance(override, dict):
                raise ValueError("override root must be an object")
        except (OSError, ValueError) as exc:
            _log(f"beat_writers override ignored ({path}): {exc}")
            override = {}
        for abbr, rows in (override.get("teams") or {}).items():
            if isinstance(rows, list):
                teams[str(abbr).upper()] = [r for r in rows if isinstance(r, dict) and r.get("handle")]
        seen = {_clean(r.get("handle", "")) for r in national}
        for row in override.get("national") or []:
            if isinstance(row, dict) and row.get("handle") and _clean(row["handle"]) not in seen:
                national.append(row)
                seen.add(_clean(row["handle"]))
        removed = {_clean(h) for h in (override.get("remove") or []) if isinstance(h, str)}
        if removed:
            teams = {
                k: [r for r in v if _clean(r.get("handle", "")) not in removed]
                for k, v in teams.items()
            }
            national = [r for r in national if _clean(r.get("handle", "")) not in removed]
    return {"teams": teams, "national": national}


def _clean(handle: str) -> str:
    return str(handle or "").strip().lstrip("@").lower()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _team_by_token(token: str) -> Team | None:
    teams = load_teams()
    token_l = token.strip().lower()
    if not token_l:
        return None
    upper = token.strip().upper()
    if upper in teams:
        return teams[upper]
    for alias, abbr in _alias_index():
        if alias == token_l:
            return teams[abbr]
    return None


def _team_in_text(text: str) -> tuple[Team, str] | None:
    """First (longest) alias appearing as a whole phrase in ``text``."""
    text_l = text.lower()
    for alias, abbr in _alias_index():
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text_l):
            return load_teams()[abbr], alias
    return None


def _has_nfl_context(topic: str) -> bool:
    return bool(NFL_VOCAB.search(topic)) or len(topic.split()) <= 3


def _abbr_in_text(topic: str) -> Team | None:
    teams = load_teams()
    for match in _ABBR_TOKEN.finditer(topic):
        token = match.group(1)
        if token not in teams:
            continue
        if token in _WORD_SHAPED_ABBRS and not (
            NFL_VOCAB.search(topic) or len(topic.split()) <= 3
        ):
            continue
        return teams[token]
    return None


def _person_in_text(topic: str) -> dict[str, Any] | None:
    topic_l = topic.lower()
    players = load_players()
    best: dict[str, Any] | None = None
    best_len = 0
    for key, row in players.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", topic_l):
            if len(key) > best_len:
                best, best_len = row, len(key)
    if best:
        return best
    if not _has_nfl_context(topic):
        return None
    # Unique last name, only with NFL context.
    last_names: dict[str, list[dict[str, Any]]] = {}
    for key, row in players.items():
        last = key.split()[-1]
        last_names.setdefault(last, []).append(row)
    tokens = set(re.findall(r"[a-z0-9'\-]+", topic_l))
    for last, rows in last_names.items():
        if len(rows) == 1 and len(last) >= 4 and last in tokens:
            return rows[0]
    return None


def _resolve_game(topic: str) -> NflEntity | None:
    match = _GAME_SPLIT.match(topic)
    if not match:
        return None
    left, right = match.group(1), match.group(2)
    # "Chiefs vs Bills vs Ravens" is a three-way comparison, not one game.
    if re.search(r"\b(?:vs\.?|versus)\b", right, re.IGNORECASE):
        return None
    a = _team_by_token(left) or (_team_in_text(left) or (None, None))[0] or _abbr_in_text(left)
    b = _team_by_token(right) or (_team_in_text(right) or (None, None))[0] or _abbr_in_text(right)
    if a and b and a.abbr != b.abbr:
        return NflEntity(kind="game", team=a, opponent=b, how="game")
    return None


def resolve(
    topic: str,
    *,
    explicit_team: str | None = None,
    explicit_player: str | None = None,
) -> NflEntity | None:
    """Resolve ``topic`` to one primary NFL entity, or None."""
    topic = (topic or "").strip()
    if explicit_team:
        team = _team_by_token(explicit_team)
        if team is None:
            _log(f"--team {explicit_team!r} is not a known abbreviation or alias; ignoring")
        elif explicit_player:
            row = load_players().get(explicit_player.strip().lower())
            return NflEntity(
                kind=(row or {}).get("role_kind", "player"),
                team=team,
                person=(row or {}).get("name") or explicit_player.strip(),
                person_handle=(row or {}).get("x_handle"),
                how="explicit",
            )
        else:
            return NflEntity(kind="team", team=team, how="explicit")
    if explicit_player:
        row = load_players().get(explicit_player.strip().lower())
        team = load_teams().get(str((row or {}).get("team") or "").upper())
        return NflEntity(
            kind=(row or {}).get("role_kind", "player"),
            team=team,
            person=(row or {}).get("name") or explicit_player.strip(),
            person_handle=(row or {}).get("x_handle"),
            how="explicit",
        )
    if not topic:
        return None

    game = _resolve_game(topic)
    if game:
        return game

    person = _person_in_text(topic)
    if person:
        team = load_teams().get(str(person.get("team") or "").upper())
        return NflEntity(
            kind=person.get("role_kind", "player"),
            team=team,
            person=person["name"],
            person_handle=person.get("x_handle"),
            how="player_map",
        )

    team = _abbr_in_text(topic)
    if team:
        return NflEntity(kind="team", team=team, how="abbr")

    hit = _team_in_text(topic)
    if hit:
        team, alias = hit
        # A bare city ("Chicago restaurants") needs NFL context; nicknames
        # and full names are unambiguous inside an NFL-scoped skill.
        if alias == team.city.lower() and not NFL_VOCAB.search(topic):
            return None
        return NflEntity(kind="team", team=team, how="alias")

    if LEAGUE_WORDS.search(topic):
        return NflEntity(kind="league", how="league")
    return None


# ---------------------------------------------------------------------------
# Lane helpers
# ---------------------------------------------------------------------------


def beat_handles(
    entity: NflEntity | dict[str, Any] | None,
    *,
    depth: str = "default",
    roster: dict[str, Any] | None = None,
) -> list[str]:
    """Handles for this run: the team's official X account(s) first (not counted
    against the caps), then beat writers capped by depth, then national insiders."""
    if entity is None:
        return []
    ent = entity if isinstance(entity, dict) else entity.as_dict()
    roster = roster or load_beat_writers()
    team_cap, national_cap = BEAT_HANDLE_CAPS.get(depth, BEAT_HANDLE_CAPS["default"])
    out: list[str] = []
    seen: set[str] = set()

    def _take(rows: list[dict[str, Any]], cap: int) -> None:
        taken = 0
        for row in rows:
            handle = str(row.get("handle") or "").strip().lstrip("@")
            if not handle or handle.lower() in seen or taken >= cap:
                continue
            seen.add(handle.lower())
            out.append(handle)
            taken += 1

    abbrs: list[str] = []
    official: list[dict[str, Any]] = []
    for key in ("team", "opponent"):
        team = ent.get(key)
        if isinstance(team, dict) and team.get("abbr"):
            abbrs.append(str(team["abbr"]).upper())
            if team.get("x_handle"):
                official.append({"handle": team["x_handle"]})
    _take(official, len(official))
    if ent.get("kind") == "game" and len(abbrs) == 2:
        half = max(1, team_cap // 2)
        _take(roster["teams"].get(abbrs[0], []), half)
        _take(roster["teams"].get(abbrs[1], []), team_cap - half)
    elif abbrs:
        _take(roster["teams"].get(abbrs[0], []), team_cap)
    _take(roster.get("national", []), national_cap)
    return out


def beat_writer_meta(
    handles: list[str], *, roster: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """Lower-cased handle → {name, outlet, role, team} for render attribution."""
    roster = roster or load_beat_writers()
    wanted = {_clean(h) for h in handles}
    meta: dict[str, dict[str, Any]] = {}
    for abbr, team in load_teams().items():
        key = _clean(team.x_handle)
        if key in wanted:
            meta[key] = {
                "name": team.name,
                "outlet": "official team account",
                "role": "official",
                "team": abbr,
            }
    for abbr, rows in roster["teams"].items():
        for row in rows:
            key = _clean(row.get("handle", ""))
            if key in wanted:
                meta[key] = {
                    "name": row.get("name", ""),
                    "outlet": row.get("outlet", ""),
                    "role": row.get("role", "beat"),
                    "team": abbr,
                }
    for row in roster.get("national", []):
        key = _clean(row.get("handle", ""))
        if key in wanted and key not in meta:
            meta[key] = {
                "name": row.get("name", ""),
                "outlet": row.get("outlet", ""),
                "role": row.get("role", "insider"),
                "team": None,
            }
    return meta


def default_subreddits(entity: NflEntity | dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """(broad subreddits, dedicated team subreddits) for the resolved entity."""
    if entity is None:
        return [], []
    ent = entity if isinstance(entity, dict) else entity.as_dict()
    dedicated: list[str] = []
    for key in ("team", "opponent"):
        team = ent.get(key)
        if isinstance(team, dict) and team.get("subreddit"):
            dedicated.append(str(team["subreddit"]))
    return ["nfl"], dedicated


def team_query_terms(team: Team | dict[str, Any]) -> list[str]:
    """Search phrases for a team: full name, nickname, city."""
    row = team if isinstance(team, dict) else team.as_dict()
    return [str(row["name"]), str(row["nickname"]), str(row["city"])]


def beat_writers_enabled(config: dict[str, Any]) -> bool:
    """``--beat-writers off`` or ``NFL30_BEAT_WRITERS=off`` disables the lane."""
    flag = config.get("_beat_writers")
    if flag is not None:
        return str(flag).strip().lower() not in {"off", "0", "false", "no"}
    value = str(config.get("NFL30_BEAT_WRITERS") or "").strip().lower()
    return value not in {"off", "0", "false", "no"}
