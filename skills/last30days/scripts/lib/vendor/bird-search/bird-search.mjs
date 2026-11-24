#!/usr/bin/env node
/**
 * bird-search.mjs - Vendored Bird CLI search wrapper for /last30days.
 * Subset of @steipete/bird v0.8.0 (MIT License, Peter Steinberger).
 *
 * Usage:
 *   node bird-search.mjs [--count N] [--timeout-ms MS] [--json] [--top] -- <query>
 *   node bird-search.mjs <query> [--count N] [--timeout-ms MS] [--json] [--top]
 *   node bird-search.mjs --whoami
 *   node bird-search.mjs --check
 */

import { redactSecrets, resolveCredentials } from './lib/cookies.js';
import { TwitterClientBase } from './lib/twitter-client-base.js';
import { withSearch } from './lib/twitter-client-search.js';

// Build a search-only client (no posting, bookmarks, etc.)
const SearchClient = withSearch(TwitterClientBase);

const args = process.argv.slice(2);

function writeStdout(text) {
  if (text) process.stdout.write(text);
}

function writeStderr(text) {
  if (text) process.stderr.write(text);
}

function parsePositiveCount(raw) {
  if (typeof raw !== 'string' || !/^\d+$/.test(raw)) {
    return null;
  }
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    return null;
  }
  return parsed;
}

async function main() {
  // Single-pass CLI parse with `--` end-of-options support. Only recognize
  // mode and option flags before `--`; everything after `--` is positional
  // query text so leading `-` queries or `--check`/`--whoami` cannot hijack
  // option parsing.
  let mode = 'search';
  let count = 20;
  let timeoutMs = 30000;
  let jsonOutput = false;
  // --top reads X's Top tab (engagement-ranked) instead of Latest. Topic
  // searches need it: on a busy topic Latest holds only the last few minutes.
  let product = 'Latest';
  const positional = [];
  let afterDoubleDash = false;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (afterDoubleDash) {
      positional.push(arg);
      continue;
    }
    if (arg === '--') {
      afterDoubleDash = true;
      continue;
    }
    if (arg === '--check') {
      mode = 'check';
    } else if (arg === '--whoami') {
      mode = 'whoami';
    } else if (arg === '--count' || arg === '-n') {
      const parsedCount = parsePositiveCount(args[i + 1]);
      if (parsedCount === null) {
        writeStderr(`Error: Invalid ${arg} value (expected positive integer)\n`);
        return 1;
      }
      count = parsedCount;
      i++;
    } else if (arg === '--timeout-ms') {
      const parsedTimeoutMs = parsePositiveCount(args[i + 1]);
      if (parsedTimeoutMs === null) {
        writeStderr(`Error: Invalid ${arg} value (expected positive integer)\n`);
        return 1;
      }
      timeoutMs = parsedTimeoutMs;
      i++;
    } else if (arg === '--json') {
      jsonOutput = true;
    } else if (arg === '--top') {
      product = 'Top';
    } else if (arg.startsWith('-')) {
      writeStderr('Unknown option\nUsage: node bird-search.mjs [--count N] [--timeout-ms MS] [--json] [--top] -- <query>\n');
      return 1;
    } else {
      positional.push(arg);
    }
  }

  // --check: verify that credentials can be resolved
  if (mode === 'check') {
    try {
      const { cookies, warnings } = await resolveCredentials({});
      if (cookies.authToken && cookies.ct0) {
        writeStdout(JSON.stringify({ authenticated: true, source: cookies.source }));
        return 0;
      }
      writeStdout(JSON.stringify({ authenticated: false, warnings }));
      return 1;
    } catch (err) {
      const msg = redactSecrets(err?.message || String(err));
      writeStdout(JSON.stringify({ authenticated: false, error: msg }));
      return 1;
    }
  }

  // --whoami: check auth and output source
  if (mode === 'whoami') {
    try {
      const { cookies } = await resolveCredentials({});
      if (cookies.authToken && cookies.ct0) {
        writeStdout(cookies.source || 'authenticated');
        return 0;
      }
      writeStderr('Not authenticated\n');
      return 1;
    } catch (err) {
      const msg = redactSecrets(err?.message || String(err));
      writeStderr(`Auth check failed: ${msg}\n`);
      return 1;
    }
  }

  const query = positional.join(' ');
  if (!query || !query.trim()) {
    writeStderr('Usage: node bird-search.mjs [--count N] [--timeout-ms MS] [--json] [--top] -- <query>\n');
    return 1;
  }

  const resolvedSecrets = [];
  try {
    // Resolve credentials (env vars, then browser cookies)
    const { cookies, warnings } = await resolveCredentials({});
    if (cookies.authToken) resolvedSecrets.push(cookies.authToken);
    if (cookies.ct0) resolvedSecrets.push(cookies.ct0);
    if (cookies.cookieHeader) resolvedSecrets.push(cookies.cookieHeader);

    if (!cookies.authToken || !cookies.ct0) {
      const msg = redactSecrets(
        warnings.length > 0 ? warnings.join('; ') : 'No Twitter credentials found',
        resolvedSecrets,
      );
      if (jsonOutput) {
        writeStdout(JSON.stringify({ error: msg, items: [] }));
      } else {
        writeStderr(`Error: ${msg}\n`);
      }
      return 1;
    }

    const client = new SearchClient({
      cookies: {
        authToken: cookies.authToken,
        ct0: cookies.ct0,
        cookieHeader: cookies.cookieHeader,
      },
      timeoutMs,
    });

    const result = await client.search(query, count, { product });

    if (!result.success) {
      const msg = redactSecrets(result.error, resolvedSecrets);
      const partialItems = Array.isArray(result.tweets) ? result.tweets : [];
      if (jsonOutput) {
        writeStdout(JSON.stringify({ error: msg, items: partialItems }));
      } else {
        writeStderr(`Search failed: ${msg}\n`);
      }
      return 1;
    }

    const tweets = result.tweets || [];
    if (jsonOutput) {
      writeStdout(JSON.stringify(tweets));
    } else {
      for (const tweet of tweets) {
        const author = tweet.author?.username || 'unknown';
        writeStdout(`@${author}: ${tweet.text?.slice(0, 200)}\n\n`);
      }
    }

    return 0;
  } catch (err) {
    const msg = redactSecrets(err?.message || String(err), resolvedSecrets);
    if (jsonOutput) {
      writeStdout(JSON.stringify({ error: msg, items: [] }));
    } else {
      writeStderr(`Error: ${msg}\n`);
    }
    return 1;
  }
}

try {
  const code = await main();
  process.exitCode = Number.isInteger(code) ? code : 1;
} catch (err) {
  const msg = redactSecrets(err?.message || String(err));
  writeStderr(`Fatal error: ${msg}\n`);
  process.exitCode = 1;
}
