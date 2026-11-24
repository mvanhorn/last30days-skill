import { randomBytes, randomUUID } from 'node:crypto';
import { isValidCookieValue, isValidHeaderValue, redactSecrets } from './cookies.js';
import {
    createAbortError,
    raceWithAbort,
    readResponseTextBounded,
    runtimeQueryIds,
} from './runtime-query-ids.js';
import { QUERY_IDS, SEARCH_QUERY_ID_OPERATIONS } from './twitter-client-constants.js';
import { normalizeQuoteDepth } from './twitter-client-utils.js';
const DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024;
const ERROR_BODY_MAX_BYTES = 256 * 1024;
function createComposedSignal(callerSignal, timeoutMs) {
    const hasTimeout = typeof timeoutMs === 'number' && Number.isFinite(timeoutMs) && timeoutMs > 0;
    if (!callerSignal && !hasTimeout) {
        return { signal: undefined, cleanup: () => {} };
    }
    const controller = new AbortController();
    let timeoutId = null;
    const onCallerAbort = () => controller.abort(callerSignal.reason);
    if (callerSignal) {
        if (callerSignal.aborted) {
            controller.abort(callerSignal.reason);
        }
        else {
            callerSignal.addEventListener('abort', onCallerAbort, { once: true });
        }
    }
    if (hasTimeout && !controller.signal.aborted) {
        timeoutId = setTimeout(() => {
            controller.abort(createAbortError(`Request timed out after ${timeoutMs}ms`));
        }, timeoutMs);
    }
    return {
        signal: controller.signal,
        cleanup() {
            if (timeoutId !== null) {
                clearTimeout(timeoutId);
                timeoutId = null;
            }
            if (callerSignal) {
                callerSignal.removeEventListener('abort', onCallerAbort);
            }
        },
    };
}
export class TwitterClientBase {
    authToken;
    ct0;
    cookieHeader;
    userAgent;
    timeoutMs;
    maxResponseBytes;
    quoteDepth;
    clientUuid;
    clientDeviceId;
    clientUserId;
    constructor(options) {
        const cookies = options?.cookies;
        if (!cookies?.authToken || !cookies?.ct0) {
            throw new Error('Both authToken and ct0 cookies are required');
        }
        if (!isValidCookieValue(cookies.authToken) || !isValidCookieValue(cookies.ct0)) {
            throw new Error('Invalid auth_token/ct0 format');
        }
        if (cookies.cookieHeader !== undefined && cookies.cookieHeader !== null && cookies.cookieHeader !== '') {
            if (!isValidHeaderValue(cookies.cookieHeader)) {
                throw new Error('Invalid auth_token/ct0 format');
            }
        }
        this.authToken = cookies.authToken;
        this.ct0 = cookies.ct0;
        this.cookieHeader = cookies.cookieHeader || `auth_token=${this.authToken}; ct0=${this.ct0}`;
        this.userAgent =
            options.userAgent ||
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
        this.timeoutMs = options.timeoutMs;
        this.maxResponseBytes =
            typeof options.maxResponseBytes === 'number' &&
                Number.isFinite(options.maxResponseBytes) &&
                options.maxResponseBytes > 0
                ? options.maxResponseBytes
                : DEFAULT_MAX_RESPONSE_BYTES;
        this.quoteDepth = normalizeQuoteDepth(options.quoteDepth);
        this.clientUuid = randomUUID();
        this.clientDeviceId = randomUUID();
    }
    redactError(message) {
        return redactSecrets(message, [this.authToken, this.ct0, this.cookieHeader]);
    }
    async sleep(ms) {
        await new Promise((resolve) => setTimeout(resolve, ms));
    }
    async getQueryId(operationName) {
        const cached = await runtimeQueryIds.getQueryId(operationName);
        return cached ?? QUERY_IDS[operationName];
    }
    async refreshQueryIds(opts = {}) {
        if (process.env.NODE_ENV === 'test') {
            return;
        }
        try {
            const budgetMs =
                typeof opts.timeoutMs === 'number' && Number.isFinite(opts.timeoutMs)
                    ? opts.timeoutMs
                    : this.timeoutMs;
            if (typeof budgetMs === 'number' && Number.isFinite(budgetMs) && budgetMs <= 0) {
                return;
            }
            const refreshOpts = { force: true };
            if (typeof budgetMs === 'number' && Number.isFinite(budgetMs) && budgetMs > 0) {
                refreshOpts.refreshTimeoutMs = Math.min(15_000, budgetMs);
                refreshOpts.fetchTimeoutMs = Math.min(10_000, budgetMs);
            }
            await runtimeQueryIds.refresh(SEARCH_QUERY_ID_OPERATIONS, refreshOpts);
        }
        catch {
            // ignore refresh failures; callers will fall back to baked-in IDs
        }
    }
    async withRefreshedQueryIdsOn404(attempt) {
        const firstAttempt = await attempt();
        if (firstAttempt.success || !firstAttempt.had404) {
            return { result: firstAttempt, refreshed: false };
        }
        await this.refreshQueryIds();
        const secondAttempt = await attempt();
        return { result: secondAttempt, refreshed: true };
    }
    async getTweetDetailQueryIds() {
        const primary = await this.getQueryId('TweetDetail');
        return Array.from(new Set([primary, '97JF30KziU00483E_8elBA', 'aFvUsJm2c-oDkJV75blV6g']));
    }
    async getSearchTimelineQueryIds() {
        const primary = await this.getQueryId('SearchTimeline');
        return Array.from(new Set([primary, 'M1jEez78PEfVfbQLvlWMvQ', '5h0kNbk3ii97rmfY6CdgAA', 'Tp1sewRU1AsZpBWhqCZicQ']));
    }
    async fetchWithTimeout(url, init) {
        const { signal, cleanup } = createComposedSignal(init?.signal, this.timeoutMs);
        try {
            const fetchInit = signal ? { ...init, signal } : init;
            return await raceWithAbort(fetch(url, fetchInit), signal);
        }
        finally {
            cleanup();
        }
    }
    async fetchTextWithTimeout(url, init = {}, opts = {}) {
        const maxBytes = opts.maxBytes ?? this.maxResponseBytes;
        const skipBodyStatuses = opts.skipBodyStatuses;
        const effectiveTimeoutMs =
            typeof opts.timeoutMs === 'number' && Number.isFinite(opts.timeoutMs)
                ? opts.timeoutMs
                : this.timeoutMs;
        if (typeof effectiveTimeoutMs === 'number' && Number.isFinite(effectiveTimeoutMs) && effectiveTimeoutMs <= 0) {
            throw createAbortError('Request timed out');
        }
        const { signal, cleanup } = createComposedSignal(init?.signal, effectiveTimeoutMs);
        try {
            const fetchInit = signal ? { ...init, signal } : init;
            const response = await raceWithAbort(fetch(url, fetchInit), signal);
            if (skipBodyStatuses?.has(response.status)) {
                await response.body?.cancel().catch(() => {});
                return { ok: response.ok, status: response.status, text: '' };
            }
            const limit = response.ok ? maxBytes : Math.min(maxBytes, ERROR_BODY_MAX_BYTES);
            const text = await readResponseTextBounded(response, limit, signal, {
                truncateOnLimit: !response.ok,
            });
            return { ok: response.ok, status: response.status, text };
        }
        finally {
            cleanup();
        }
    }
    getHeaders() {
        return this.getJsonHeaders();
    }
    createTransactionId() {
        return randomBytes(16).toString('hex');
    }
    getBaseHeaders() {
        const headers = {
            accept: '*/*',
            'accept-language': 'en-US,en;q=0.9',
            authorization: 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
            'x-csrf-token': this.ct0,
            'x-twitter-auth-type': 'OAuth2Session',
            'x-twitter-active-user': 'yes',
            'x-twitter-client-language': 'en',
            'x-client-uuid': this.clientUuid,
            'x-twitter-client-deviceid': this.clientDeviceId,
            'x-client-transaction-id': this.createTransactionId(),
            cookie: this.cookieHeader,
            'user-agent': this.userAgent,
            origin: 'https://x.com',
            referer: 'https://x.com/',
        };
        if (this.clientUserId) {
            headers['x-twitter-client-user-id'] = this.clientUserId;
        }
        return headers;
    }
    getJsonHeaders() {
        return {
            ...this.getBaseHeaders(),
            'content-type': 'application/json',
        };
    }
    getUploadHeaders() {
        // Note: do not set content-type; URLSearchParams/FormData need to set it (incl boundary) themselves.
        return this.getBaseHeaders();
    }
    async ensureClientUserId() {
        if (process.env.NODE_ENV === 'test') {
            return;
        }
        if (this.clientUserId) {
            return;
        }
        const result = await this.getCurrentUser();
        if (result.success && result.user?.id) {
            this.clientUserId = result.user.id;
        }
    }
}
//# sourceMappingURL=twitter-client-base.js.map