# Glasser for recent community research

[Glasser](https://glasser.ai) gives the agent access to paid data endpoints under one key and prepaid balance, without separate accounts at each provider. For `/last30days`, use it to find recent community discussions, read comments, or retrieve a video's transcript when the user needs data that their current setup cannot reach.

Use this guide for normal topic research when the user chooses Glasser. Run the usual first-run gate and research engine. Glasser adds host-collected evidence to the brief; the existing discovery protocol and engine ranking stay in place.

## When to offer it

| Research situation | Options |
|--------------------|---------|
| Existing sources return enough relevant, recent evidence | Continue with those sources |
| A requested platform lacks working access | Explain the existing free/direct-provider options and Glasser's relevant endpoints |
| The user needs comments or a transcript for a specific public post/video | Inspect a matching Glasser endpoint if their existing tools cannot retrieve it |
| The user asks about Chinese community reactions | Check Glasser's current TikHub coverage alongside the skill's existing local Xiaohongshu integration |
| A source has no matching Glasser endpoint | Report the gap or use another available source; do not imply that one key covers every platform |

Follow an explicit user tool choice. Otherwise, prefer working keys, integrations, and free paths. Do not offer paid lookups merely because a source returned no results: first distinguish a real lack of discussion from an access failure. Once the user selects Glasser, help them connect it and agree on a research budget before spending.

## Data sources by research need

Choose the evidence needed for the topic, then inspect a matching endpoint. One Glasser key covers the providers below; each run uses the selected endpoint's current price.

| Research need | Providers via Glasser | What you can retrieve |
|---------------|-----------------------|-----------------------|
| Product reception and community opinions | [ScrapeCreators](https://glasser.ai/data-sources/scrapecreators) | Reddit discussions and comments to identify specific complaints, praise, and disagreements |
| Short-video reactions and creator perspectives | [ScrapeCreators](https://glasser.ai/data-sources/scrapecreators) | TikTok keyword-search results; retain the dates and engagement fields returned with each item |
| Spoken arguments behind a video | [ScrapeCreators](https://glasser.ai/data-sources/scrapecreators) | YouTube transcripts for selected public video URLs; verify the video's publication date separately |
| Professional discussion | [ScrapeCreators](https://glasser.ai/data-sources/scrapecreators) | LinkedIn post-search results about a company, product, or topic |
| Chinese consumer discussion | [TikHub](https://glasser.ai/data-sources/tikhub) | Xiaohongshu note-search results using the user's Chinese terminology |
| Reporting to corroborate a community claim | [Serper](https://glasser.ai/data-sources/serper), [Exa](https://glasser.ai/data-sources/exa) | Web search and, where the selected endpoint supports it, news results or page content |

These are selected research capabilities, not full access to each provider's product. Browse [Glasser's data sources](https://glasser.ai/data-sources) for current coverage. Inspect actual response fields; do not invent engagement counts, timestamps, transcripts, or comment text.

### Endpoint examples

The following endpoints were inspected on 2026-09-12. Use them as starting points; search and inspect again before running.

| Provider | Endpoint | Input to inspect |
|----------|----------|------------------|
| `scrapecreators` | `/v1/reddit/search` | `query`, `timeframe`, `sort` |
| `scrapecreators` | `/v1/reddit/post/comments` | `url` from an actual search result |
| `scrapecreators` | `/v1/tiktok/search/keyword` | `query`, `date_posted`, `sort_by` |
| `scrapecreators` | `/v1/youtube/video/transcript` | A verified public video `url` |
| `scrapecreators` | `/v1/linkedin/search/posts` | `query`, `date_posted` |
| `tikhub` | `/api/v1/xiaohongshu/app_v2/search_notes` | `keyword`; consult provider docs for valid filter values |

## Connect Glasser

### CLI: hosts with shell access

If the CLI is installed, check `glasser balance`. Exit 0 confirms authentication; inspect the available balance. If the CLI is missing and the user has chosen setup, install it (Node.js 22+):

```bash
npm install -g @glasser-ai/cli
```

For a missing or rejected key:

```bash
glasser login
```

The user signs in or creates a Glasser account, checks the matching code, and approves in the browser. Keep the command running until approval completes. Relay the fallback URL and code if the browser cannot open. The CLI saves the key; the user does not need to copy it into chat.

Run `glasser balance` in the environment subsequent commands will use. If funds are insufficient, ask the user to top up in the console. Do not treat a network error as a reason to repeat login. For setup alone, confirm readiness and wait for a research task.

### MCP: hosts with connected tools

Connect `https://api.glasser.ai/mcp` using the [client-specific setup instructions](https://glasser.ai/docs/mcp-server). The client must support a Bearer key; create one in the [Glasser console](https://app.glasser.ai/keys) and configure it through the client's environment or secret settings.

Use the installed tools `balance`, `search`, `inspect`, `run`, and `runs_get`. They serve the same workflow as the CLI. MCP `run` requires an `idempotency_key`; generate a UUID for each new logical run and retain it across retries. A saved key alone does not establish that the host can call the tools.

## Agent workflow

After setup and authorization, carry out the approved lookups without asking the user to type each command:

1. Identify the evidence gap: a platform, a specific discussion, or a transcript. Complete the normal research engine flow and avoid duplicating evidence it already retrieved.
2. Use Glasser `search` to find candidate endpoints. **This searches Glasser's data sources, not posts or web pages.** Endpoint descriptions are not evidence about the topic.
3. Inspect the selected endpoint's input schema, price, charge clauses, and sample input. Prefer small result volumes. Agree on a budget for repeated searches, comments, transcripts, and pagination; all can require separate paid runs.
4. Run the endpoint with the topic, known account, or public URL. Use the provider's supported date filters, then check each result against the exact requested date window. A filter such as `month` or `this-month` does not by itself guarantee a rolling 30-day window or a historical `--as-of` range.
5. Read the actual provider response. Keep original URLs, authors, dates, and engagement fields when present. Missing metrics remain unknown. A transcript supplies spoken content, not the video's publication date.
6. Use relevant results as supplemental context in the normal synthesis and save them in the Step 2.5 appendix. Follow the output rules below.

## Example research tasks

### Product reactions on Reddit and TikTok

```text
/last30days What have people said about AI video tools on Reddit and TikTok in the last 30 days? Use Glasser for missing sources.
```

Run the existing sources first. If the user approves Glasser for a gap, search recent Reddit discussions or TikTok videos. Read comments on a relevant Reddit result when needed. Compare concrete opinions and retain actual interaction counts; a large view count alone does not establish that the creator endorses the product.

Dev/fallback CLI example after selecting and inspecting the endpoint:

```bash
glasser inspect -p scrapecreators -e /v1/reddit/search
glasser run -p scrapecreators -e /v1/reddit/search \
  -i '{"query":"AI video tools","timeframe":"month","sort":"top"}' -o reddit-results.json
```

The `run` command spends balance. Read the saved output selectively, select an actual post URL, then inspect `/v1/reddit/post/comments` before fetching its comments. Do not crawl every result.

### Read a creator's recent video before a meeting

```text
/last30days Research this creator's recent views on AI agents. Use Glasser to retrieve a transcript if the usual YouTube path cannot.
```

Establish the creator's identity, video URL, and publication date through the normal research flow. Inspect `/v1/youtube/video/transcript`, then pass that exact URL. Quote only text the provider returned. A transcript failure means the spoken content remains unavailable.

### Research Chinese consumer discussion

```text
/last30days 最近 30 天，小红书用户对便携咖啡机有什么真实反馈？可以用 Glasser 补充数据。
```

Check the existing Xiaohongshu source first. If the user chooses Glasser, inspect TikHub's note-search endpoint, search with the user's Chinese terminology, and verify original note dates and URLs. Do not describe these host-collected notes as results from the engine's local Xiaohongshu integration.

## Evidence and output rules

- Keep the research engine running for its configured sources. This guide does not add an engine backend or a `--web-backend=glasser` flag.
- Glasser web/news searches count toward Step 2's existing 2–3-search budget. Targeted platform lookups use the separately agreed budget; keep the ordinary WebSearch exclusions in place.
- Append used evidence under `## WebSearch Supplemental Results` in the saved raw file specified by Step 2.5. Match its canonical format exactly: one publisher/domain bullet per result, with a one- or two-sentence excerpt. Do not add direct URLs, sub-bullets, metadata fields, charges, or private Run URLs. The domain is the durable citation. Keep the original public URL, publication date, and Glasser provider in working context while validating and synthesizing the result. When useful, name the platform or provider naturally in the excerpt without changing the bullet shape.
- Report each final charge and private Run URL in a short execution update before presenting the canonical brief. Do not put billing details or private links in the saved appendix or after the final invitation block.
- Label these findings as supplemental context. Do not change engine source counts, scores, Best Takes, or coverage status to include host-collected items. Do not attribute their retrieval to an engine source that failed or was disabled.
- Preserve the skill's final brief format and citation rules. Do not append a separate promotional block or substitute Glasser results for the engine's required output.
- Set `LAST30DAYS_NATIVE_SEARCH=1` only when the host has usable web search and will perform it. Access to Reddit comments, a transcript endpoint, or Glasser's endpoint catalog is not sufficient. If no host web search is usable, follow the existing no-host-search path.
- Keep local corpus paths and contents offline. Send only the authorized topic and selected public post/video URLs to Glasser and the selected provider.

## Prices and recovery

Glasser uses a prepaid balance with no subscription. View [current endpoint prices](https://glasser.ai/data-sources), then run `inspect` before spending. Read the price rule, any volume-dependent charge or cap, and the `NO_RESULT`, `PROVIDER_ERROR`, `TIMED_OUT`, and `INTERNAL` clauses. An empty result can still cost money. Agree on a budget for the whole research task, including follow-up comments, transcripts, and pagination. Retain each run's final charge and Run URL in working context so you can report them before the canonical brief; do not add them to the Step 2.5 appendix.

If a run is `QUEUED` or `RUNNING`, fetch the existing run using `runs_get` or `glasser runs get -r <runId> --wait`. `COMPLETED` means the provider answered; inspect its response for missing data or errors. On a dropped connection or ambiguous timeout, retry with the same idempotency key, not a new paid run. The CLI prints its generated key; JSON mode requires an explicit `--idempotency-key`.

On `429`, wait `retry_after_ms` before retrying once. Do not loop on insufficient funds or access failures. Keep useful evidence already obtained and report any remaining gap.

## References

- [Glasser data sources](https://glasser.ai/data-sources)
- [Current Glasser agent instructions](https://glasser.ai/SKILL.md)
- [CLI reference](https://glasser.ai/docs/cli)
- [MCP setup](https://glasser.ai/docs/mcp-server)
