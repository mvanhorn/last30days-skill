**Amazon buyer signals** — a new opt-in `amazon` source, backed by the Bright Data CLI. On shopping-intent topics it pulls discovered products with live ratings and prices, plus a capped sample of recent written reviews woven in as buyer voice.

The signal it exists for is *drift*: an all-time rating from thousands of ratings set against the average of only the reviews inside the last 30 days. When those disagree, something changed this month, and the review text says what. The emoji footer names each product and the direction it moved — `📦 Amazon: 3 products │ Chill Max XL 4.4★→3.8★ ↓, Deluxe Bag 4.7★→5.0★, BLUEY Set 4.8★ new` — rather than reporting inventory counts.

Off by default and dual-gated: the `brightdata` CLI must be on PATH and logged in, *and* the run must ask for the source (`--search ...,amazon` or `INCLUDE_SOURCES=amazon`). It never auto-fires from inferred intent. Use `--amazon-query` when the product keyword differs from the topic — a person topic searches their company's product line, not their name. `LAST30DAYS_AMAZON_DOMAIN` selects a non-US marketplace.

Billing is one credit per request against a 5,000/month free tier, so a typical run costs 4 credits regardless of how many reviews come back.
