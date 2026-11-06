# Vendored: bird-search

Search-only subset of [`@steipete/bird`](https://github.com/steipete/bird) v0.8.0 (MIT, see `LICENSE`), run by `lib/bird_x.py` as `node bird-search.mjs`.

## What this code does

- Talks only to `x.com` (GraphQL search, plus the page and `abs.twimg.com` JS bundles it reads to refresh rotating query IDs). It doesn't `eval` anything it fetches.
- Gets credentials only from its environment (`AUTH_TOKEN` / `CT0`), which `lib/bird_x.py` fills from an allowlisted environment.
- Writes only its query-ID and feature caches under `~/.config/bird/`.

## last30days modifications (relative to upstream)

- `lib/cookies.js` reads the environment only. Upstream's browser cookie extraction, a lazy load of the unvendored `@steipete/sweet-cookie`, is removed. Browser reads belong to the Python engine, which requires consent.
- `lib/twitter-client-constants.js`: the write endpoints (tweet post, media upload, media metadata) are removed.
- Every file in this directory is included in install-time security scans. Nothing here is listed in `.skillignore`.

## Integrity manifest

`tests/test_vendor_integrity.py` checks every file against this list (sha256 of the file with LF line endings). If you change vendored code on purpose, review the diff, then regenerate the list with:

```bash
python3 tests/test_vendor_integrity.py --print-manifest
```

<!-- manifest:start -->
62316704df7426e5a79d2827ff8aca36e9abb3a73b8e68557030749ebefec667  LICENSE
584482e894c38bbde7a915013f741974fa8ea722ac34c56704dccf184f7c3d56  bird-search.mjs
b0a0fb345cd1879f5ad9037131142773108e93e3edf44ef309b09d7856a6d609  lib/cookies.js
36361f4cae0040255f84709459ebb1f0131566a567269ed7b99d073cad930f66  lib/features.json
5f20227de1d68250ad59b7ad593db4bfb1d0f1a21694963eab3ed413c2d0d5c0  lib/paginate-cursor.js
eba47a9d26f87d0b4eb8a4f554833ed8d11b3d8728e363deeae702d09f87fc02  lib/query-ids.json
f41614d62b6c91dfc85fc4af389e6f49435324bf26460b715a991486609579d8  lib/runtime-features.js
0f5c61dbaad3de7fb44a14c57ed005228e51f9fc361472eff59bff4cb2a1199e  lib/runtime-query-ids.js
ea972b7894458e78edd1927d102a052ae45726a7c967b376ac7f5544f5b65499  lib/twitter-client-base.js
074d474833017f8790de83d36335d00ae00f6e437f18a545df3f4164e2d20dfc  lib/twitter-client-constants.js
4b4f2048069f751a821777cb2084f3b9126d99a958494fef71f0699d8145c4dd  lib/twitter-client-features.js
3dfbe18ea54215d9a6393ff0884d9977dd9ddf0884a06486bdaee8a4e30d4616  lib/twitter-client-search.js
6450f74c664db958d703e09288184ef1ca5f622782f9e11a5a9bfa3d3c78c534  lib/twitter-client-types.js
b230db5583760ccd0b5b5fb1592f449a7d0bc92f6184808e8ffb6f8968df20e9  lib/twitter-client-utils.js
96b65305969a179ee7a7735f1b475599d6035f32fda1fd030ea57ee09380cdb5  package.json
<!-- manifest:end -->
