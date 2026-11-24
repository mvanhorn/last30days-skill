Hardened HTTP response size bounds, entity/keyless URL SSRF checks, and robots.txt wildcard matching:

- Enforce bounded response reads (16 MiB default body cap, 64 KiB error-body cap) in `lib/http.py` and `lib/bounded_get.py`, stream `bounded_get` bodies through a private temporary file with capped JSON metadata capture instead of base64-over-pipe amplification, and close 3xx redirect streams without unbounded `fp.read()`.
- Validate official-site domains and URLs in `lib/resolve.py` and `lib/company_resolve.py` via `urlsplit` and `lib/public_host.py`, rejecting IP literals, reserved local/internal suffixes, userinfo, non-standard ports, non-2-letter ccSLD look-alikes, and unsafe single-statement Wikidata P856 URLs; enforce `public_only=True` and safe decode/error handling in `official_site_socials`.
- Replace regex backtracking in `lib/job_boards/robots.py` with a linear-time wildcard matcher and bounded parser limits to eliminate `robots.txt` ReDoS.
- Validate target URLs in `lib/web_fetch_keyless.py` against private/loopback/metadata/userinfo/internal targets, enforce `public_only=True` plus an `r.jina.ai` HTTPS redirect guard, and handle decode failures cleanly.
