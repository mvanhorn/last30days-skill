# Agent JSON export

The agent JSON profile is the stable machine-readable research contract for downstream agents, scripts, dashboards, and workflow tools. Ask the slash command for machine-readable JSON:

```text
/last30days AI coding agents — return the versioned agent JSON export
```

For direct engine use in scripts, cron jobs, or development, use:

```bash
python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json
python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json --output results.json
```

`--emit=json` defaults to `--json-profile=agent`. The full internal report remains available for debugging and power users:

```bash
python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json --json-profile=raw
```

The raw profile is intentionally unversioned and may change when pipeline internals change. It preserves the JSON serialization used before the agent profile was introduced.

### Local corpus privacy

Evidence from `--corpus` / `LAST30DAYS_CORPUS_DIRS` is excluded from the versioned agent profile by default. The exclusion removes corpus results, corpus-only clusters, corpus source outcomes, freshness verdicts, and titles derived from a corpus representative. Set `LAST30DAYS_CORPUS_IN_EXPORT=1` only for a run whose JSON is intentionally allowed to contain local file contents. This opt-in does not change the schema shape or version; it permits `source: "corpus"` entries in the existing result fields. The unversioned `raw` profile is a complete local debug dump and may contain corpus paths and text.

## Discovery export

Discovery mode has a separate versioned contract so its topic results do not change the normal research export:

```bash
python3 skills/last30days/scripts/last30days.py --discover "AI agents" --emit=json
```

Its top level contains `schema_version` (`1.0`), `kind` (`"discovery"`), `domain`, `generated_at`, `window_days`, `source_status`, `feeds`, `results`, and `warnings`. Each ranked result contains `rank`, `topic`, `why_spiking`, `momentum` (`new-this-week` or `building`), `velocity_score`, `sources`, per-source native `engagement`, a ready-to-run `command`, and `evidence_urls`. The discovery contract follows the same versioning policy below but evolves independently of the normal agent export. `--json-profile=raw` returns the unversioned internal `DiscoveryReport` dataclass instead.

When `LAST30DAYS_API_KEY` and `LAST30DAYS_API_BASE` route a run through a configured remote API, the server does not return the local `Report` needed to build this profile. In that mode, `--json-profile=agent` exits with status 2 instead of emitting a misleading shape; use `--json-profile=raw` to retain the remote backend's existing server-response JSON contract.

## Top-level fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | Agent export contract version. The current version is `1.2`. |
| `query` | string | The research topic supplied to the engine. |
| `generated_at` | string | UTC generation timestamp in RFC 3339 format. |
| `window_days` | integer | Number of days between the report's start and end dates. |
| `source_status` | object | Map of source name to the outcome observed during this run. |
| `freshness_verdicts` | array | Per-claim act-time verdicts produced by `--verify-freshness`; empty when verification was not requested or no conservative claims were extractable. |
| `clusters` | array | Ranked groups of related results. |
| `results` | array | Ranked, flat evidence results for downstream processing. |

All top-level fields are always present. Empty runs contain empty `clusters` and `results` arrays. Sources appear in `source_status` when the run recorded an outcome for them.

## `freshness_verdicts`

Each entry identifies the grounded claim and candidate, its primary source item, the typed `verdict` (`current`, `stale`, `contradicted`, or `unsupported`), the original and re-derived values when applicable, and source/evidence URLs and timestamps. `stale` means a successful point re-fetch returned a moved value; `contradicted` means a newer item in the report window explicitly disagrees; `unsupported` means the datum could not be re-checked, including degraded `source_status` outcomes. Consumers can gate actions on `verdict == "current"` without treating an unreachable source as evidence that a claim moved.

## `source_status`

Each value distinguishes a clean empty result from incomplete coverage:

| State | Meaning |
| --- | --- |
| `ok` | The source completed and returned one or more items. |
| `no-results` | The source completed successfully but found no matching items. |
| `partial` | The source returned some items before a later failure. |
| `rate-limited` | Retrieval was stopped by a provider rate limit. |
| `auth-failed` | Credentials were missing, rejected, or expired during retrieval. |
| `unreachable` | The source or network endpoint could not be reached. |
| `timeout` | Retrieval exceeded its time limit. |
| `schema-drift` | The provider response no longer matched the expected shape. |
| `skipped-unconfigured` | The source was intentionally skipped because required configuration was absent. |
| `error` | Retrieval failed for another reason. |

Consumers must not interpret failure states as evidence that a source had no discussion. Only `no-results` means the source completed cleanly with zero matches.

## Cluster fields

| Field | Type | Meaning |
| --- | --- | --- |
| `title` | string | Cluster headline. |
| `summary` | string | Summary from the cluster's representative ranked result. |
| `sources` | array of strings | Sources represented by the cluster. |
| `engagement_total` | number | Sum of one headline native engagement counter per result. Known sources use their primary count (for example, Digg uses `postCount`); otherwise the largest counter-like field is used. Ranking, ratio, rating, and computed-score metadata are excluded. |

Cluster array order is ranking order. A result's `cluster` value is the zero-based index into this array.

## Result fields

| Field | Type | Meaning |
| --- | --- | --- |
| `candidate_id` | string | Stable identifier joining this result to `freshness_verdicts[].candidate_id`. Added in `1.2`. |
| `title` | string | Result title. |
| `source` | string | Primary source name, such as `reddit`, `x`, `youtube`, or `grounding`. |
| `url` | string | Canonical result URL. It may be empty when the provider supplies no link. |
| `published_at` | string | Primary source item's publication date or timestamp. Omitted when unknown. |
| `summary` | string | Normalized snippet, with the relevance explanation or body used as fallback. |
| `engagement` | object | Native engagement counters from the primary source item, such as Reddit `score` and `num_comments` or X `likes` and `reposts`. |
| `relevance_score` | number | Engine final score normalized to the inclusive `0.0`–`1.0` range. |
| `cluster` | integer | Zero-based index into `clusters`. Omitted when the result is not assigned to a cluster. |

Fields whose value is unknown are omitted rather than emitted as JSON `null`. Strings and collection fields otherwise remain present, including empty strings, objects, or arrays.

## Comparison runs

Comparison queries use an envelope so each entity keeps its own contract:

```json
{
  "schema_version": "1.2",
  "comparison": true,
  "entities": ["OpenAI", "Anthropic"],
  "reports": [
    {"entity": "OpenAI", "report": {"schema_version": "1.2", "query": "OpenAI"}},
    {"entity": "Anthropic", "report": {"schema_version": "1.2", "query": "Anthropic"}}
  ]
}
```

The abbreviated reports above only illustrate the envelope; real reports contain every documented top-level field.

## Versioning policy

- `schema_version` uses `major.minor` numbering.
- Any breaking field removal, rename, type change, semantic change, or envelope change requires a major-version bump.
- Backward-compatible field additions may use a minor-version bump. Consumers should ignore fields they do not recognize.
- The checked-in golden snapshot test locks the complete current shape. Contract changes must update the version and snapshot deliberately.
- `1.2` added `candidate_id` to each `results` entry so verdicts can be joined to the result they annotate.
- `--json-profile=raw` is outside this compatibility policy because it mirrors internal pipeline dataclasses.

`--preflight --emit=json` is a different machine contract for permission and configuration inspection. `--json-profile` does not alter preflight output.
