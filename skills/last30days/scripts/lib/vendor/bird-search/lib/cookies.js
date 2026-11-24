/**
 * Browser cookie extraction for Twitter authentication.
 * Delegates to @steipete/sweet-cookie for Safari/Chrome/Firefox reads.
 */
const TWITTER_COOKIE_NAMES = ['auth_token', 'ct0'];
const TWITTER_URL = 'https://x.com/';
const TWITTER_ORIGINS = ['https://x.com/', 'https://twitter.com/'];
const DEFAULT_COOKIE_TIMEOUT_MS = 30_000;
export const COOKIE_VALUE_REGEX = /^[!#$%&'()*+\-./0-9:<=>?@A-Z[\]^_`a-z{|}~]+$/;
const HEADER_VALUE_REGEX = /^[\x20-\x7e]+$/;
const INVALID_COOKIE_ERROR = 'Invalid auth_token/ct0 format';
const SECRET_ENV_KEYS = ['AUTH_TOKEN', 'CT0', 'TWITTER_AUTH_TOKEN', 'TWITTER_CT0'];
export function isValidCookieValue(value) {
    return typeof value === 'string' && COOKIE_VALUE_REGEX.test(value);
}
export function isValidHeaderValue(value) {
    return typeof value === 'string' && HEADER_VALUE_REGEX.test(value);
}
function normalizeCookieValue(value) {
    if (typeof value !== 'string') {
        return null;
    }
    if (/[\r\n\0]/.test(value)) {
        throw new Error(INVALID_COOKIE_ERROR);
    }
    const trimmed = value.replace(/^[ \t]+|[ \t]+$/g, '');
    if (trimmed.length === 0) {
        return null;
    }
    if (!COOKIE_VALUE_REGEX.test(trimmed)) {
        throw new Error(INVALID_COOKIE_ERROR);
    }
    return trimmed;
}
export function redactSecrets(text, extraSecrets = []) {
    if (text === null || text === undefined) {
        return '';
    }
    let out = String(text);
    out = out.replace(
        /(Headers\.\w+:\s*)(["'])[\s\S]*?\2(\s+is an invalid header value)/gi,
        '$1"<redacted>"$3',
    );
    out = out.replace(/\b(auth_token|ct0)=[^;\s"']*/gi, '$1=<redacted>');
    const candidates = new Set();
    const addCandidate = (raw) => {
        if (typeof raw !== 'string') {
            return;
        }
        for (const variant of [raw, raw.trim()]) {
            if (variant.length >= 6) {
                candidates.add(variant);
                const escaped = JSON.stringify(variant).slice(1, -1);
                if (escaped.length >= 6) {
                    candidates.add(escaped);
                }
            }
        }
    };
    for (const secret of extraSecrets) {
        addCandidate(secret);
    }
    for (const key of SECRET_ENV_KEYS) {
        addCandidate(process.env[key]);
    }
    const ordered = [...candidates].sort((a, b) => b.length - a.length);
    for (const secret of ordered) {
        out = out.split(secret).join('<redacted>');
    }
    return out;
}
async function loadSweetCookie() {
    return import('@steipete/sweet-cookie');
}
function normalizeValue(value) {
    if (typeof value !== 'string') {
        return null;
    }
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
}
function envFlagEnabled(name) {
    const value = normalizeValue(process.env[name]);
    if (!value) {
        return false;
    }
    return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase());
}
function cookieHeader(authToken, ct0) {
    if (!isValidCookieValue(authToken) || !isValidCookieValue(ct0)) {
        throw new Error(INVALID_COOKIE_ERROR);
    }
    return `auth_token=${authToken}; ct0=${ct0}`;
}
function buildEmpty() {
    return { authToken: null, ct0: null, cookieHeader: null, source: null };
}
function readEnvCookie(cookies, keys, field) {
    if (cookies[field]) {
        return;
    }
    for (const key of keys) {
        const value = normalizeCookieValue(process.env[key]);
        if (!value) {
            continue;
        }
        cookies[field] = value;
        if (!cookies.source) {
            cookies.source = `env ${key}`;
        }
        break;
    }
}
function resolveSources(cookieSource) {
    if (Array.isArray(cookieSource)) {
        return cookieSource;
    }
    if (cookieSource) {
        return [cookieSource];
    }
    return ['safari', 'chrome', 'firefox'];
}
function labelForSource(source, profile) {
    if (source === 'safari') {
        return 'Safari';
    }
    if (source === 'chrome') {
        return profile ? `Chrome profile "${profile}"` : 'Chrome default profile';
    }
    return profile ? `Firefox profile "${profile}"` : 'Firefox default profile';
}
function pickCookieValue(cookies, name) {
    const matches = cookies.filter((c) => c?.name === name && typeof c.value === 'string');
    if (matches.length === 0) {
        return null;
    }
    const preferred = matches.find((c) => (c.domain ?? '').endsWith('x.com'));
    if (preferred?.value) {
        return preferred.value;
    }
    const twitter = matches.find((c) => (c.domain ?? '').endsWith('twitter.com'));
    if (twitter?.value) {
        return twitter.value;
    }
    return matches[0]?.value ?? null;
}
async function readTwitterCookiesFromBrowser(options) {
    const warnings = [];
    const out = buildEmpty();
    const { getCookies } = options;
    const { cookies, warnings: providerWarnings } = await getCookies({
        url: TWITTER_URL,
        origins: TWITTER_ORIGINS,
        names: [...TWITTER_COOKIE_NAMES],
        browsers: [options.source],
        mode: 'merge',
        chromeProfile: options.chromeProfile,
        firefoxProfile: options.firefoxProfile,
        timeoutMs: options.cookieTimeoutMs,
    });
    warnings.push(...providerWarnings);
    const authToken = normalizeCookieValue(pickCookieValue(cookies, 'auth_token'));
    const ct0 = normalizeCookieValue(pickCookieValue(cookies, 'ct0'));
    if (authToken) {
        out.authToken = authToken;
    }
    if (ct0) {
        out.ct0 = ct0;
    }
    if (out.authToken && out.ct0) {
        out.cookieHeader = cookieHeader(out.authToken, out.ct0);
        out.source = labelForSource(options.source, options.source === 'chrome' ? options.chromeProfile : options.firefoxProfile);
        return { cookies: out, warnings };
    }
    if (options.source === 'safari') {
        warnings.push('No Twitter cookies found in Safari. Make sure you are logged into x.com in Safari.');
    }
    else if (options.source === 'chrome') {
        warnings.push('No Twitter cookies found in Chrome. Make sure you are logged into x.com in Chrome.');
    }
    else {
        warnings.push('No Twitter cookies found in Firefox. Make sure you are logged into x.com in Firefox and the profile exists.');
    }
    return { cookies: out, warnings };
}
async function extractCookiesFromBrowser(options) {
    const { getCookies } = await loadSweetCookie();
    return readTwitterCookiesFromBrowser({
        getCookies,
        ...options,
    });
}
export async function extractCookiesFromSafari() {
    return extractCookiesFromBrowser({ source: 'safari' });
}
export async function extractCookiesFromChrome(profile) {
    return extractCookiesFromBrowser({ source: 'chrome', chromeProfile: profile });
}
export async function extractCookiesFromFirefox(profile) {
    return extractCookiesFromBrowser({ source: 'firefox', firefoxProfile: profile });
}
/**
 * Resolve Twitter credentials from multiple sources.
 * Priority: CLI args > environment variables > browsers (ordered).
 */
export async function resolveCredentials(options) {
    const warnings = [];
    const cookies = buildEmpty();
    const disableBrowserCookies = envFlagEnabled('BIRD_DISABLE_BROWSER_COOKIES') ||
        envFlagEnabled('LAST30DAYS_DISABLE_BROWSER_COOKIES');
    const cookieTimeoutMs = typeof options.cookieTimeoutMs === 'number' &&
        Number.isFinite(options.cookieTimeoutMs) &&
        options.cookieTimeoutMs > 0
        ? options.cookieTimeoutMs
        : process.platform === 'darwin'
            ? DEFAULT_COOKIE_TIMEOUT_MS
            : undefined;
    if (options.authToken !== undefined && options.authToken !== null) {
        const normalizedAuthToken = normalizeCookieValue(options.authToken);
        if (normalizedAuthToken) {
            cookies.authToken = normalizedAuthToken;
            cookies.source = 'CLI argument';
        }
    }
    if (options.ct0 !== undefined && options.ct0 !== null) {
        const normalizedCt0 = normalizeCookieValue(options.ct0);
        if (normalizedCt0) {
            cookies.ct0 = normalizedCt0;
            if (!cookies.source) {
                cookies.source = 'CLI argument';
            }
        }
    }
    readEnvCookie(cookies, ['AUTH_TOKEN', 'TWITTER_AUTH_TOKEN'], 'authToken');
    readEnvCookie(cookies, ['CT0', 'TWITTER_CT0'], 'ct0');
    if (cookies.authToken && cookies.ct0) {
        cookies.cookieHeader = cookieHeader(cookies.authToken, cookies.ct0);
        return { cookies, warnings };
    }
    if (disableBrowserCookies) {
        if (!cookies.authToken) {
            warnings.push('Missing auth_token - provide via --auth-token, AUTH_TOKEN env var, or disable BIRD_DISABLE_BROWSER_COOKIES to allow browser cookie lookup');
        }
        if (!cookies.ct0) {
            warnings.push('Missing ct0 - provide via --ct0, CT0 env var, or disable BIRD_DISABLE_BROWSER_COOKIES to allow browser cookie lookup');
        }
        return { cookies, warnings };
    }
    const sourcesToTry = resolveSources(options.cookieSource);
    let getCookies;
    try {
        ({ getCookies } = await loadSweetCookie());
    }
    catch (error) {
        if (error?.code !== 'ERR_MODULE_NOT_FOUND' ||
            !String(error.message ?? '').includes('@steipete/sweet-cookie')) {
            throw error;
        }
        warnings.push('Browser cookie lookup unavailable because vendored dependency @steipete/sweet-cookie is not installed.');
        if (!cookies.authToken) {
            warnings.push('Missing auth_token - provide via --auth-token, AUTH_TOKEN env var, or login to x.com in Safari/Chrome/Firefox');
        }
        if (!cookies.ct0) {
            warnings.push('Missing ct0 - provide via --ct0, CT0 env var, or login to x.com in Safari/Chrome/Firefox');
        }
        return { cookies, warnings };
    }
    for (const source of sourcesToTry) {
        const res = await readTwitterCookiesFromBrowser({
            getCookies,
            source,
            chromeProfile: options.chromeProfile,
            firefoxProfile: options.firefoxProfile,
            cookieTimeoutMs,
        });
        warnings.push(...res.warnings);
        if (res.cookies.authToken && res.cookies.ct0) {
            return { cookies: res.cookies, warnings };
        }
    }
    if (!cookies.authToken) {
        warnings.push('Missing auth_token - provide via --auth-token, AUTH_TOKEN env var, or login to x.com in Safari/Chrome/Firefox');
    }
    if (!cookies.ct0) {
        warnings.push('Missing ct0 - provide via --ct0, CT0 env var, or login to x.com in Safari/Chrome/Firefox');
    }
    if (cookies.authToken && cookies.ct0) {
        cookies.cookieHeader = cookieHeader(cookies.authToken, cookies.ct0);
    }
    return { cookies, warnings };
}
//# sourceMappingURL=cookies.js.map
