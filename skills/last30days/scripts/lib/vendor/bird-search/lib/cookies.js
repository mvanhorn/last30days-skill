/**
 * Twitter credential resolution for the vendored search client.
 *
 * last30days modification: environment-only. Browser cookie extraction (the
 * upstream lazy load of an unvendored browser-cookie npm package) is removed. The Python
 * engine is the single, consent-gated owner of browser reads; it passes the
 * resulting AUTH_TOKEN / CT0 through this process's environment
 * (lib/bird_x.py). This file never reads a browser and never loads code that
 * is not vendored here.
 */
const ENV_NAMES = {
    authToken: ['AUTH_TOKEN', 'TWITTER_AUTH_TOKEN'],
    ct0: ['CT0', 'TWITTER_CT0'],
};
function normalizeValue(value) {
    if (typeof value !== 'string') {
        return null;
    }
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
}
function cookieHeader(authToken, ct0) {
    return `auth_token=${authToken}; ct0=${ct0}`;
}
function buildEmpty() {
    return { authToken: null, ct0: null, cookieHeader: null, source: null };
}
function readEnvCookie(cookies, names, field) {
    if (cookies[field]) {
        return;
    }
    for (const name of names) {
        const value = normalizeValue(process.env[name]);
        if (!value) {
            continue;
        }
        cookies[field] = value;
        if (!cookies.source) {
            cookies.source = `env ${name}`;
        }
        break;
    }
}
/**
 * Resolve Twitter credentials. Priority: explicit options > environment.
 */
export async function resolveCredentials(options = {}) {
    const warnings = [];
    const cookies = buildEmpty();
    if (options.authToken) {
        cookies.authToken = options.authToken;
        cookies.source = 'CLI argument';
    }
    if (options.ct0) {
        cookies.ct0 = options.ct0;
        if (!cookies.source) {
            cookies.source = 'CLI argument';
        }
    }
    readEnvCookie(cookies, ENV_NAMES.authToken, 'authToken');
    readEnvCookie(cookies, ENV_NAMES.ct0, 'ct0');
    if (cookies.authToken && cookies.ct0) {
        cookies.cookieHeader = cookieHeader(cookies.authToken, cookies.ct0);
        return { cookies, warnings };
    }
    if (!cookies.authToken) {
        warnings.push('Missing auth_token - provide it via the AUTH_TOKEN env var');
    }
    if (!cookies.ct0) {
        warnings.push('Missing ct0 - provide it via the CT0 env var');
    }
    return { cookies, warnings };
}
