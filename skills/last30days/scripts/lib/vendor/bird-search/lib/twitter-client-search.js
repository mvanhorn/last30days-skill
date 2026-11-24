import { redactSecrets } from './cookies.js';
import { QUERY_ID_REGEX } from './runtime-query-ids.js';
import { TWITTER_API_BASE } from './twitter-client-constants.js';
import { buildSearchFeatures } from './twitter-client-features.js';
import { extractCursorFromInstructions, parseTweetsFromInstructions } from './twitter-client-utils.js';
const RAW_QUERY_MISSING_REGEX = /must be defined/i;
const DEFAULT_UNLIMITED_MAX_PAGES = 20;
const DEFAULT_FINITE_MAX_PAGES_CEILING = 10;
const SKIP_BODY_ON_404 = new Set([404]);
function isQueryIdMismatch(payload) {
    try {
        const parsed = JSON.parse(payload);
        return (parsed.errors?.some((error) => {
            if (error?.extensions?.code === 'GRAPHQL_VALIDATION_FAILED') {
                return true;
            }
            if (error?.path?.includes('rawQuery') && RAW_QUERY_MISSING_REGEX.test(error.message ?? '')) {
                return true;
            }
            return false;
        }) ?? false);
    }
    catch {
        return false;
    }
}
export function withSearch(Base) {
    class TwitterClientSearch extends Base {
        // biome-ignore lint/complexity/noUselessConstructor lint/suspicious/noExplicitAny: TS mixin constructor requirement.
        constructor(...args) {
            super(...args);
        }
        /**
         * Search for tweets matching a query
         */
        async search(query, count = 20, options = {}) {
            return this.searchPaged(query, count, options);
        }
        /**
         * Get all search results (paged)
         */
        async getAllSearchResults(query, options) {
            return this.searchPaged(query, Number.POSITIVE_INFINITY, options);
        }
        async searchPaged(query, limit, options = {}) {
            if (limit !== Number.POSITIVE_INFINITY && (!Number.isFinite(limit) || limit <= 0)) {
                return { success: false, error: 'Invalid search count' };
            }
            const features = buildSearchFeatures();
            const pageSize = 20;
            const seen = new Set();
            const tweets = [];
            let cursor = options.cursor;
            let nextCursor;
            let pagesFetched = 0;
            const { includeRaw = false, maxPages, product = 'Latest', pageDelayMs = 0 } = options;
            if (maxPages !== undefined) {
                if (typeof maxPages !== 'number' || !Number.isFinite(maxPages)) {
                    return { success: false, error: 'Invalid maxPages' };
                }
                if (maxPages <= 0) {
                    return { success: true, tweets: [], nextCursor: cursor };
                }
            }
            const unlimited = limit === Number.POSITIVE_INFINITY;
            const effectiveMaxPages = maxPages !== undefined
                ? Math.floor(maxPages)
                : unlimited
                    ? DEFAULT_UNLIMITED_MAX_PAGES
                    : Math.min(DEFAULT_FINITE_MAX_PAGES_CEILING, Math.ceil(limit / pageSize) + 2);
            if (effectiveMaxPages <= 0) {
                return { success: true, tweets: [], nextCursor: cursor };
            }
            const seenCursors = new Set(cursor ? [cursor] : []);
            const totalTimeoutMs =
                typeof options.timeoutMs === 'number' && Number.isFinite(options.timeoutMs) && options.timeoutMs > 0
                    ? options.timeoutMs
                    : typeof this.timeoutMs === 'number' && Number.isFinite(this.timeoutMs) && this.timeoutMs > 0
                        ? this.timeoutMs
                        : undefined;
            const deadlineMs = totalTimeoutMs !== undefined ? Date.now() + totalTimeoutMs : undefined;
            const remainingBudgetMs = () => (deadlineMs !== undefined ? deadlineMs - Date.now() : undefined);
            const timeoutError = () => `Request timed out after ${totalTimeoutMs}ms`;
            const redact = (msg) =>
                typeof this.redactError === 'function' ? this.redactError(msg) : redactSecrets(msg);
            const fetchPage = async (pageCount, pageCursor) => {
                let lastError;
                let had404 = false;
                const queryIds = await this.getSearchTimelineQueryIds();
                for (const queryId of queryIds) {
                    if (typeof queryId !== 'string' || !QUERY_ID_REGEX.test(queryId)) {
                        continue;
                    }
                    const remainingMs = remainingBudgetMs();
                    if (remainingMs !== undefined && remainingMs <= 0) {
                        return { success: false, error: timeoutError(), had404: false };
                    }
                    const variables = {
                        rawQuery: query,
                        count: pageCount,
                        querySource: 'typed_query',
                        product,
                        ...(pageCursor ? { cursor: pageCursor } : {}),
                    };
                    const params = new URLSearchParams({
                        variables: JSON.stringify(variables),
                    });
                    const url = `${TWITTER_API_BASE}/${queryId}/SearchTimeline?${params.toString()}`;
                    try {
                        const response = await this.fetchTextWithTimeout(
                            url,
                            {
                                method: 'POST',
                                headers: this.getHeaders(),
                                body: JSON.stringify({ features, queryId }),
                            },
                            {
                                skipBodyStatuses: SKIP_BODY_ON_404,
                                ...(remainingMs !== undefined ? { timeoutMs: remainingMs } : {}),
                            },
                        );
                        if (response.status === 404) {
                            had404 = true;
                            lastError = `HTTP ${response.status}`;
                            continue;
                        }
                        if (!response.ok) {
                            const text = response.text;
                            const shouldRefreshQueryIds = (response.status === 400 || response.status === 422) && isQueryIdMismatch(text);
                            return {
                                success: false,
                                error: `HTTP ${response.status}: ${redact(text).slice(0, 200)}`,
                                had404: had404 || shouldRefreshQueryIds,
                            };
                        }
                        const data = JSON.parse(response.text);
                        if (data.errors && data.errors.length > 0) {
                            const shouldRefreshQueryIds = data.errors.some((error) => error?.extensions?.code === 'GRAPHQL_VALIDATION_FAILED');
                            return {
                                success: false,
                                error: redact(data.errors.map((e) => e.message).join(', ')),
                                had404: had404 || shouldRefreshQueryIds,
                            };
                        }
                        const instructions = data.data?.search_by_raw_query?.search_timeline?.timeline?.instructions;
                        const pageTweets = parseTweetsFromInstructions(instructions, { quoteDepth: this.quoteDepth, includeRaw });
                        const nextCursor = extractCursorFromInstructions(instructions);
                        return { success: true, tweets: pageTweets, cursor: nextCursor, had404 };
                    }
                    catch (error) {
                        lastError = redact(error instanceof Error ? error.message : String(error));
                        return { success: false, error: lastError, had404: false };
                    }
                }
                return { success: false, error: lastError ?? 'Unknown error fetching search results', had404 };
            };
            const fetchWithRefresh = async (pageCount, pageCursor) => {
                const firstAttempt = await fetchPage(pageCount, pageCursor);
                if (firstAttempt.success) {
                    return firstAttempt;
                }
                if (firstAttempt.had404) {
                    const refreshRemainingMs = remainingBudgetMs();
                    if (refreshRemainingMs !== undefined && refreshRemainingMs <= 0) {
                        return { success: false, error: timeoutError() };
                    }
                    await this.refreshQueryIds(
                        refreshRemainingMs !== undefined ? { timeoutMs: refreshRemainingMs } : {},
                    );
                    const retryRemainingMs = remainingBudgetMs();
                    if (retryRemainingMs !== undefined && retryRemainingMs <= 0) {
                        return { success: false, error: timeoutError() };
                    }
                    const secondAttempt = await fetchPage(pageCount, pageCursor);
                    if (secondAttempt.success) {
                        return secondAttempt;
                    }
                    return { success: false, error: secondAttempt.error };
                }
                return { success: false, error: firstAttempt.error };
            };
            while (unlimited || tweets.length < limit) {
                const remainingBeforePage = remainingBudgetMs();
                if (remainingBeforePage !== undefined && remainingBeforePage <= 0) {
                    if (tweets.length > 0) {
                        return { success: false, error: timeoutError(), tweets, nextCursor: cursor };
                    }
                    return { success: false, error: timeoutError() };
                }
                if (pagesFetched > 0 && pageDelayMs > 0 && typeof this.sleep === 'function') {
                    if (remainingBeforePage !== undefined && pageDelayMs >= remainingBeforePage) {
                        return { success: false, error: timeoutError(), tweets, nextCursor: cursor };
                    }
                    await this.sleep(pageDelayMs);
                }
                const pageCount = unlimited ? pageSize : Math.min(pageSize, limit - tweets.length);
                const page = await fetchWithRefresh(pageCount, cursor);
                if (!page.success) {
                    if (tweets.length > 0) {
                        return { success: false, error: page.error, tweets, nextCursor: cursor };
                    }
                    return { success: false, error: page.error };
                }
                pagesFetched += 1;
                let added = 0;
                for (const tweet of page.tweets) {
                    if (seen.has(tweet.id)) {
                        continue;
                    }
                    seen.add(tweet.id);
                    tweets.push(tweet);
                    added += 1;
                    if (!unlimited && tweets.length >= limit) {
                        break;
                    }
                }
                const pageCursor = page.cursor;
                if (!pageCursor || seenCursors.has(pageCursor) || page.tweets.length === 0 || added === 0) {
                    nextCursor = undefined;
                    break;
                }
                seenCursors.add(pageCursor);
                if (pagesFetched >= effectiveMaxPages) {
                    nextCursor = pageCursor;
                    break;
                }
                cursor = pageCursor;
                nextCursor = pageCursor;
            }
            return { success: true, tweets, nextCursor };
        }
    }
    return TwitterClientSearch;
}
//# sourceMappingURL=twitter-client-search.js.map