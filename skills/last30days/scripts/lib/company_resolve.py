"""Resolve a research topic to the company it names, through Wikidata.

One conservative lookup, keyless. A wrong company or a wrong ticker is far
worse than none, so every step that is not certain returns nothing:

- The company is certain when Wikidata's top search hit for the topic carries
  the topic as its label or an alias and is a business, or is a person who is
  the current chief executive of exactly one organization, itself a business.
  There is no hop from a product to its maker or from a subsidiary to its
  parent.
- The one hop is downward, from a company that is already certain to the names
  its own Wikidata record lists, and it is made only for a caller that asks
  for them (``names=True``). A holding company files little under its own
  name, so a caller searching filings needs these, and it treats each one as a
  name that certainly belongs to the company. So only names that could be a
  legal filing name and that say something new are returned, compared by the
  key filings are matched by (case, punctuation and trailing legal designators
  removed):

  - Own names, at most ``MAX_OWN_NAMES``: the English official names, current
    ones first, then the English and all-language label and aliases. One is
    kept when its key has at least two words, it ends in a legal designator
    (a nickname such as "Big Blue" does not), it is not a domain, it carries
    no parenthetical, and its key is not the company's own or one already
    kept. "Microsoft Corp." adds nothing to "Microsoft"; "MS" and
    "microsoft.com" could be anyone.
  - Subsidiary names, at most ``MAX_SUBSIDIARY_NAMES``: the labels of the
    current subsidiaries, then of what the company owns, in the record's
    order. One is kept when its key has at least three characters, is not
    already kept, and does not extend the company's own name ("Microsoft
    Algeria" is already reachable from "Microsoft").

  The names are not checked to be companies, and a long list of subsidiaries
  is not returned whole.
- The ticker is certain when the company has exactly one primary listing:
  the one listing marked preferred, or the only listing when none is marked.
  A listing with an end time is ignored. Two share classes or two exchanges
  with none marked give no ticker. They give ``ticker_candidates`` instead:
  the symbols of the current listings, for a caller that can confirm one of
  them against a second source. A candidate is never a ticker on its own.

Any error, timeout or unexpected response returns nothing and logs once. That
nothing is a ``FailedLookup``, so a caller can tell "Wikidata did not answer"
from "Wikidata answered and nothing is certain". The one exception is the
one request for the subsidiaries' labels, made only when the names are asked
for: the company is certain before it is made, so its failure logs once and
costs only the subsidiary names.
"""

from __future__ import annotations

import re
from typing import Any

from . import http, log
from .company_names import name_key

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
TIMEOUT_SECONDS = 5

_INSTANCE_OF = "P31"
_SUBCLASS_OF = "P279"
_STOCK_EXCHANGE = "P414"
_TICKER_SYMBOL = "P249"
_CHIEF_EXECUTIVE = "P169"
_OFFICIAL_WEBSITE = "P856"
_END_TIME = "P582"
_OFFICIAL_NAME = "P1448"
_SUBSIDIARY = "P355"
_OWNER_OF = "P1830"

MAX_OWN_NAMES = 3
MAX_SUBSIDIARY_NAMES = 12
MAX_COMPANY_NAMES = MAX_OWN_NAMES + MAX_SUBSIDIARY_NAMES
# Labels are asked for more subsidiaries than are kept, so one that is
# filtered out does not cost a later one its place. One request takes 50 ids.
_MAX_LABEL_IDS = 50
_DOMAIN = re.compile(r"(?:[\w-]+\.)+[a-z]{2,}", re.IGNORECASE)

_HUMAN = "Q5"
# business, enterprise, company, public company, corporation. "organization"
# is left out on purpose: bands, parties and constituencies sit under it.
_BUSINESS_CLASSES = frozenset({"Q4830453", "Q6881511", "Q783794", "Q891723", "Q167037"})
# A person recorded as chief executive of more organizations than this is not
# checked further; the answer would be "more than one" or a very large fetch.
_MAX_LED_ORGANIZATIONS = 5

_GENERIC_TOPIC_WORDS = frozenset({
    "how", "what", "why", "best", "top", "tutorial", "guide", "prompts",
    "news", "latest", "ideas", "examples",
})
_KNOWN_SINGLE_WORD_COMPANIES = frozenset({
    "apple", "uber", "google", "microsoft", "amazon", "meta", "netflix",
    "openai", "anthropic", "qualtrics", "stripe", "brex",
})
_COMMON_TECH_TERMS = frozenset({
    "python", "ruby", "javascript", "typescript", "java", "go", "golang",
    "rust", "php", "swift", "kotlin", "scala", "clojure", "elixir",
    "react", "vue", "angular", "svelte", "node", "django", "rails",
    "postgres", "mysql", "redis", "kubernetes", "docker",
})


class FailedLookup(dict):
    """The empty result of a lookup that failed, as opposed to one Wikidata
    answered. It is an empty dict to every caller that does not ask."""


def company_topic_likely(topic: str) -> bool:
    """Whether a topic is shaped like a company or a person's name.

    A cheap shape check, with no network: questions, long topics and generic
    research phrasing are not company topics.
    """
    text = topic.strip()
    if not text:
        return False
    lower = text.lower()
    if "?" in text or len(text.split()) > 4:
        return False
    if any(word in _GENERIC_TOPIC_WORDS for word in lower.split()):
        return False
    if " vs " in lower or " versus " in lower:
        parts = re.split(r"\s+(?:vs|versus)\s+", text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2:
            return False
        return _comparison_side_company_like(parts[0]) or _comparison_side_company_like(parts[1])
    return bool(text[:1].isupper() or lower in _KNOWN_SINGLE_WORD_COMPANIES)


def _comparison_side_company_like(side: str) -> bool:
    token = re.sub(r"[^\w.+#-]", "", side.strip().split()[0] if side.strip() else "")
    if not token:
        return False
    lower = token.lower()
    if lower in _COMMON_TECH_TERMS:
        return False
    return bool(token[:1].isupper() or lower in _KNOWN_SINGLE_WORD_COMPANIES)


def resolve_company(
    topic: str, *, listing: bool = True, names: bool = False,
) -> dict[str, Any]:
    """Return what is certain about the company a topic names.

    Keys, each present only when resolved: ``company``, ``company_site``,
    ``ticker``, ``exchange``, ``ticker_candidates`` (the current listings'
    symbols, only when the company is certain and its ticker is not),
    ``company_names`` (only with ``names``; other names that certainly belong
    to the company: its official names and aliases that could be a legal
    filing name, then its subsidiaries, at most ``MAX_COMPANY_NAMES`` distinct
    strings and never ``company`` itself), and
    ``company_via_person`` (True) when the topic is the person who leads the
    company. An empty dict means nothing is certain; it is a ``FailedLookup``
    when the lookup itself failed. No request is made for a topic that is not
    company-shaped. With ``listing`` off the company's stock listing is not
    read, so there is no ``ticker``, ``exchange`` or ``ticker_candidates``.
    Without ``names`` the company's other names are not read: there is no
    ``company_names`` and no request for the subsidiaries' labels. Only a
    caller that searches filings under those names needs them.
    """
    if not company_topic_likely(topic):
        return {}
    try:
        return _resolve(" ".join(topic.split()), listing, names)
    except Exception as exc:
        log.source_log(
            "Company",
            f"Wikidata lookup failed for {topic!r}: {type(exc).__name__}: {exc}",
            tty_only=False,
        )
        return FailedLookup()


def _resolve(topic: str, listing: bool = True, names: bool = False) -> dict[str, Any]:
    hits = _api({
        "action": "wbsearchentities", "search": topic, "language": "en",
        "uselang": "en", "type": "item", "limit": 1,
    })["search"]
    if not hits:
        return {}
    top_id = hits[0]["id"]
    entity = _entities([top_id], "labels|aliases|claims")[top_id]
    if topic.casefold() not in _names(entity):
        return {}
    if _HUMAN in _item_ids(_statements(entity, _INSTANCE_OF)):
        company = _company_led_by(top_id)
        if company is None:
            return {}
        return {**_company_fields(company, listing, names), "company_via_person": True}
    if not _is_business(entity):
        return {}
    return _company_fields(entity, listing, names)


def _api(params: dict[str, Any]) -> dict[str, Any]:
    data = http.get(
        WIKIDATA_API,
        params={**params, "format": "json"},
        timeout=TIMEOUT_SECONDS,
        retries=1,
    )
    if not isinstance(data, dict) or "error" in data:
        raise ValueError(f"unexpected Wikidata response for {params.get('action')}")
    return data


def _entities(ids: list[str], props: str) -> dict[str, dict[str, Any]]:
    found = _api({
        "action": "wbgetentities", "ids": "|".join(ids), "props": props,
        "languages": "en|mul",
    })["entities"]
    for entity_id in ids:
        if "missing" in found[entity_id]:
            raise ValueError(f"Wikidata has no entity {entity_id}")
    return found


def _names(entity: dict[str, Any]) -> set[str]:
    """English and all-language labels and aliases, case-folded."""
    names = {label["value"] for label in entity.get("labels", {}).values()}
    for aliases in entity.get("aliases", {}).values():
        names.update(alias["value"] for alias in aliases)
    return {" ".join(name.split()).casefold() for name in names}


def _statements(entity: dict[str, Any], prop: str) -> list[dict[str, Any]]:
    """Statements that hold a value and are not deprecated."""
    return [
        statement
        for statement in entity.get("claims", {}).get(prop, [])
        if statement["rank"] != "deprecated" and statement["mainsnak"]["snaktype"] == "value"
    ]


def _preferred_or_all(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred = [statement for statement in statements if statement["rank"] == "preferred"]
    return preferred or statements


def _value(statement: dict[str, Any]) -> Any:
    return statement["mainsnak"]["datavalue"]["value"]


def _item_ids(statements: list[dict[str, Any]]) -> set[str]:
    return {_value(statement)["id"] for statement in statements}


def _ended(statement: dict[str, Any]) -> bool:
    return _END_TIME in statement.get("qualifiers", {})


def _is_business(entity: dict[str, Any]) -> bool:
    """True when the entity is an instance of a business class, or of a class
    that is itself a direct subclass of one (an automobile manufacturer)."""
    classes = _item_ids(_statements(entity, _INSTANCE_OF))
    if classes & _BUSINESS_CLASSES:
        return True
    if not classes:
        return False
    parents = _entities(sorted(classes), "claims")
    return any(
        _item_ids(_statements(parent, _SUBCLASS_OF)) & _BUSINESS_CLASSES
        for parent in parents.values()
    )


def _company_led_by(person_id: str) -> dict[str, Any] | None:
    """The one business whose current chief executive is this person."""
    found = _api({
        "action": "query", "list": "search", "srprop": "",
        "srsearch": f"haswbstatement:{_CHIEF_EXECUTIVE}={person_id}",
        "srlimit": _MAX_LED_ORGANIZATIONS + 1,
    })["query"]
    ids = [hit["title"] for hit in found["search"]]
    if not ids or len(ids) > _MAX_LED_ORGANIZATIONS or found["searchinfo"]["totalhits"] > len(ids):
        return None
    organizations = _entities(ids, "labels|aliases|claims")
    led = [
        organization
        for organization in organizations.values()
        if any(
            _value(statement)["id"] == person_id and not _ended(statement)
            for statement in _preferred_or_all(_statements(organization, _CHIEF_EXECUTIVE))
        )
    ]
    if len(led) != 1 or not _is_business(led[0]):
        return None
    return led[0]


def _company_fields(
    entity: dict[str, Any], listing: bool = True, names: bool = False,
) -> dict[str, Any]:
    labels = entity["labels"]
    fields: dict[str, Any] = {"company": (labels.get("en") or labels["mul"])["value"]}
    sites = _preferred_or_all(_statements(entity, _OFFICIAL_WEBSITE))
    if len(sites) == 1:
        fields["company_site"] = _value(sites[0])
    if listing:
        fields.update(_primary_listing(entity))
    if names:
        other_names = _company_names(entity, fields["company"])
        if other_names:
            fields["company_names"] = other_names
    return fields


def _own_names(entity: dict[str, Any]) -> list[str]:
    """Official names in English, current ones first, then the English and
    all-language label and aliases."""
    official = [
        statement
        for statement in _statements(entity, _OFFICIAL_NAME)
        if _value(statement)["language"].split("-")[0] in ("en", "mul")
    ]
    official.sort(key=_ended)
    names = [_value(statement)["text"] for statement in official]
    labels, aliases = entity.get("labels", {}), entity.get("aliases", {})
    names.extend(labels[language]["value"] for language in ("en", "mul") if language in labels)
    names.extend(
        alias["value"] for language in ("en", "mul") for alias in aliases.get(language, [])
    )
    return names


def _subsidiary_ids(entity: dict[str, Any]) -> list[str]:
    """Current subsidiaries, then what the company owns, in the record's order."""
    ids = [
        _value(statement)["id"]
        for prop in (_SUBSIDIARY, _OWNER_OF)
        for statement in _statements(entity, prop)
        if not _ended(statement)
    ]
    return list(dict.fromkeys(ids))


def _subsidiary_names(entity: dict[str, Any]) -> list[str]:
    """Labels of the subsidiaries the company lists, from one request. A
    failure here is logged and gives none: the company is already certain."""
    ids = _subsidiary_ids(entity)[:_MAX_LABEL_IDS]
    if not ids:
        return []
    try:
        found = _entities(ids, "labels")
        labels = [found[entity_id].get("labels", {}) for entity_id in ids]
        return [
            (label.get("en") or label["mul"])["value"]
            for label in labels
            if "en" in label or "mul" in label
        ]
    except Exception as exc:
        log.source_log(
            "Company",
            f"Wikidata subsidiary names failed for {entity.get('id')}: "
            f"{type(exc).__name__}: {exc}",
            tty_only=False,
        )
        return []


def _carries_designator(name: str, key: str) -> bool:
    """True when the name ends in a legal designator ("... Corporation"), which
    a nickname ("Big Blue") never does: the key is the name without one."""
    plain = " ".join(re.sub(r"[^\w\s]", " ", name).casefold().split())
    return plain != key


def _company_names(entity: dict[str, Any], company: str) -> list[str]:
    """Own names that could be a legal filing name, then subsidiary names,
    each new under the key filings are matched by."""
    company_key = name_key(company)
    seen = {company_key}
    own: list[str] = []
    for name in _own_names(entity):
        name = " ".join(name.split())
        key = name_key(name)
        if len(own) == MAX_OWN_NAMES:
            break
        if key in seen or " " not in key or "(" in name or _DOMAIN.fullmatch(name):
            continue
        if not _carries_designator(name, key):
            continue
        seen.add(key)
        own.append(name)
    subsidiaries: list[str] = []
    for name in _subsidiary_names(entity):
        name = " ".join(name.split())
        key = name_key(name)
        if len(subsidiaries) == MAX_SUBSIDIARY_NAMES:
            break
        if key in seen or len(key) < 3 or key.startswith(company_key + " "):
            continue
        seen.add(key)
        subsidiaries.append(name)
    return own + subsidiaries


def _listing_tickers(statement: dict[str, Any]) -> list[str]:
    return [
        snak["datavalue"]["value"]
        for snak in statement.get("qualifiers", {}).get(_TICKER_SYMBOL, [])
        if snak["snaktype"] == "value"
    ]


def _primary_listing(entity: dict[str, Any]) -> dict[str, Any]:
    current = [
        statement for statement in _statements(entity, _STOCK_EXCHANGE) if not _ended(statement)
    ]
    primary = _preferred_or_all(current)
    tickers = _listing_tickers(primary[0]) if len(primary) == 1 else []
    if len(tickers) != 1:
        candidates = list(dict.fromkeys(
            ticker for statement in current for ticker in _listing_tickers(statement)
        ))
        return {"ticker_candidates": candidates} if candidates else {}
    exchange_id = _value(primary[0])["id"]
    labels = _entities([exchange_id], "labels")[exchange_id]["labels"]
    listing = {"ticker": tickers[0]}
    label = labels.get("en") or labels.get("mul")
    if label:
        listing["exchange"] = label["value"]
    return listing
