**Zero-click Amazon setup.** First-run setup now offers Amazon buyer signals to every user and, on acceptance, arranges the whole thing: it installs the Bright Data CLI and registers a free account through your existing GitHub login. No browser, no signup form, no credit card. Requires the `gh` CLI installed and authenticated; without it the offer is skipped rather than shown.

Accepting makes the `amazon` source available without editing `INCLUDE_SOURCES`. That changes *availability* only — whether the source runs on a given topic stays a per-run judgment the model makes, so a default-on lane does not start spending credits on topics with no product dimension.

The consent question names what leaves your machine: your GitHub numeric id, username, and public email (if set) go to Bright Data to create the account, and a private gist is created on your GitHub account for a few seconds to prove the account is yours, then deleted. Declining installs nothing and creates no account.

Free tier is 5,000 requests a month; a typical shopping run spends 4.
