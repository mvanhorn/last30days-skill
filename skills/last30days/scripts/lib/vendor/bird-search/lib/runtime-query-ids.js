import { randomBytes } from 'node:crypto';
import { chmod, lstat, mkdir, readFile, realpath, rename, unlink, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
const DEFAULT_CACHE_FILENAME = 'query-ids-cache.json';
const DEFAULT_TTL_MS = 24 * 60 * 60 * 1000;
const DEFAULT_FETCH_TIMEOUT_MS = 10_000;
const DEFAULT_REFRESH_TIMEOUT_MS = 15_000;
const DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024;
const DEFAULT_MAX_BUNDLES = 30;
const DEFAULT_MAX_REDIRECTS = 5;
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);
const ALLOWED_DISCOVERY_HOSTS = new Set(['x.com', 'abs.twimg.com']);
const DISCOVERY_PAGES = [
    'https://x.com/?lang=en',
    'https://x.com/explore',
    'https://x.com/notifications',
    'https://x.com/settings/profile',
];
const BUNDLE_URL_REGEX = /https:\/\/abs\.twimg\.com\/responsive-web\/client-web(?:-legacy)?\/[A-Za-z0-9.-]+\.js/g;
export const QUERY_ID_REGEX = /^[a-zA-Z0-9_-]+$/;
const FORBIDDEN_KEYS = new Set(['__proto__', 'constructor', 'prototype']);
const OPERATION_PATTERNS = [
    {
        regex: /e\.exports=\{queryId\s*:\s*["']([^"']+)["']\s*,\s*operationName\s*:\s*["']([^"']+)["']/gs,
        operationGroup: 2,
        queryIdGroup: 1,
    },
    {
        regex: /e\.exports=\{operationName\s*:\s*["']([^"']+)["']\s*,\s*queryId\s*:\s*["']([^"']+)["']/gs,
        operationGroup: 1,
        queryIdGroup: 2,
    },
    {
        regex: /operationName\s*[:=]\s*["']([^"']+)["'](.{0,4000}?)queryId\s*[:=]\s*["']([^"']+)["']/gs,
        operationGroup: 1,
        queryIdGroup: 3,
    },
    {
        regex: /queryId\s*[:=]\s*["']([^"']+)["'](.{0,4000}?)operationName\s*[:=]\s*["']([^"']+)["']/gs,
        operationGroup: 3,
        queryIdGroup: 1,
    },
];
const HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
    Accept: 'text/html,application/json;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
};
export function createAbortError(reason) {
    if (reason instanceof Error) {
        return reason;
    }
    const message = typeof reason === 'string' && reason ? reason : 'The operation was aborted';
    const err = new Error(message);
    err.name = 'AbortError';
    return err;
}
export async function raceWithAbort(promise, signal) {
    if (!signal) {
        return promise;
    }
    if (signal.aborted) {
        promise.catch(() => {});
        throw createAbortError(signal.reason);
    }
    promise.catch(() => {});
    let onAbort;
    const abortPromise = new Promise((_, reject) => {
        onAbort = () => reject(createAbortError(signal.reason));
        signal.addEventListener('abort', onAbort, { once: true });
    });
    try {
        return await Promise.race([promise, abortPromise]);
    }
    finally {
        signal.removeEventListener('abort', onAbort);
    }
}
export function validateDiscoveryUrl(rawUrl) {
    let parsed;
    try {
        parsed = rawUrl instanceof URL ? rawUrl : new URL(rawUrl);
    }
    catch {
        throw new Error(`Invalid discovery URL: ${String(rawUrl)}`);
    }
    if (parsed.protocol !== 'https:') {
        throw new Error(`Blocked non-HTTPS discovery URL: ${parsed.protocol}`);
    }
    if (parsed.username || parsed.password) {
        throw new Error('Blocked discovery URL with credentials');
    }
    if (parsed.port !== '') {
        throw new Error(`Blocked discovery URL with non-default port: ${parsed.port}`);
    }
    const host = parsed.hostname.toLowerCase();
    if (!ALLOWED_DISCOVERY_HOSTS.has(host)) {
        throw new Error(`Blocked discovery URL host: ${host}`);
    }
    return parsed;
}
function truncateUtf8Bytes(buf, maxBytes) {
    if (maxBytes <= 0) {
        return '';
    }
    let end = Math.min(buf.byteLength, maxBytes);
    if (end < buf.byteLength && (buf[end] & 0xc0) === 0x80) {
        let start = end - 1;
        while (start > 0 && (buf[start] & 0xc0) === 0x80) {
            start -= 1;
        }
        end = start;
    }
    return buf.subarray(0, end).toString('utf8');
}
export async function readResponseTextBounded(response, maxBytes, signal, { truncateOnLimit = false } = {}) {
    if (signal?.aborted) {
        await response.body?.cancel().catch(() => {});
        throw createAbortError(signal.reason);
    }
    const contentLengthHeader = response.headers?.get?.('content-length');
    if (!truncateOnLimit && contentLengthHeader) {
        const declared = Number.parseInt(contentLengthHeader, 10);
        if (Number.isFinite(declared) && declared > maxBytes) {
            await response.body?.cancel().catch(() => {});
            throw new Error(`Response exceeded ${maxBytes} byte limit`);
        }
    }
    if (response.body && typeof response.body.getReader === 'function') {
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let totalBytes = 0;
        let text = '';
        const onAbort = () => {
            reader.cancel().catch(() => {});
        };
        if (signal) {
            signal.addEventListener('abort', onAbort, { once: true });
        }
        try {
            while (true) {
                const { done, value } = await raceWithAbort(reader.read(), signal);
                if (signal?.aborted) {
                    throw createAbortError(signal.reason);
                }
                if (done) {
                    break;
                }
                if (value && value.byteLength > 0) {
                    if (totalBytes + value.byteLength > maxBytes) {
                        if (truncateOnLimit) {
                            const remaining = Math.max(0, maxBytes - totalBytes);
                            if (remaining > 0) {
                                text += decoder.decode(value.subarray(0, remaining), { stream: true });
                            }
                            await reader.cancel().catch(() => {});
                            break;
                        }
                        await reader.cancel().catch(() => {});
                        throw new Error(`Response exceeded ${maxBytes} byte limit`);
                    }
                    totalBytes += value.byteLength;
                    text += decoder.decode(value, { stream: true });
                }
            }
            if (signal?.aborted) {
                throw createAbortError(signal.reason);
            }
            text += decoder.decode();
            return text;
        }
        finally {
            if (signal) {
                signal.removeEventListener('abort', onAbort);
            }
            try {
                reader.releaseLock();
            }
            catch {
                // ignore
            }
        }
    }
    let text = null;
    if (typeof response.text === 'function') {
        text = await raceWithAbort(response.text(), signal);
    }
    else if (typeof response.json === 'function') {
        const parsed = await raceWithAbort(response.json(), signal);
        text = JSON.stringify(parsed ?? null);
    }
    if (text !== null) {
        if (signal?.aborted) {
            throw createAbortError(signal.reason);
        }
        const buf = Buffer.from(text, 'utf8');
        if (buf.byteLength > maxBytes) {
            if (truncateOnLimit) {
                return truncateUtf8Bytes(buf, maxBytes);
            }
            throw new Error(`Response exceeded ${maxBytes} byte limit`);
        }
        return text;
    }
    return '';
}
async function fetchText(fetchImpl, url, opts = {}) {
    const fetchTimeoutMs = opts.fetchTimeoutMs ?? DEFAULT_FETCH_TIMEOUT_MS;
    const maxBytes = opts.maxBytes ?? DEFAULT_MAX_RESPONSE_BYTES;
    const maxRedirects = opts.maxRedirects ?? DEFAULT_MAX_REDIRECTS;
    const deadlineMs = opts.deadlineMs;
    const callerSignal = opts.signal;

    let currentUrl = validateDiscoveryUrl(url).toString();
    const controller = new AbortController();
    let timeoutId = null;
    const onCallerAbort = () => controller.abort(callerSignal.reason);
    if (callerSignal) {
        if (callerSignal.aborted) {
            throw createAbortError(callerSignal.reason);
        }
        callerSignal.addEventListener('abort', onCallerAbort, { once: true });
    }
    const remainingMs = deadlineMs !== undefined ? deadlineMs - Date.now() : fetchTimeoutMs;
    const effectiveTimeoutMs = Math.min(fetchTimeoutMs, remainingMs);
    if (!Number.isFinite(effectiveTimeoutMs) || effectiveTimeoutMs <= 0) {
        if (callerSignal) {
            callerSignal.removeEventListener('abort', onCallerAbort);
        }
        throw createAbortError('Discovery refresh timed out');
    }
    timeoutId = setTimeout(() => {
        controller.abort(createAbortError(`Discovery request timed out after ${effectiveTimeoutMs}ms`));
    }, effectiveTimeoutMs);

    try {
        let redirects = 0;
        while (true) {
            const response = await raceWithAbort(
                fetchImpl(currentUrl, {
                    headers: HEADERS,
                    redirect: 'manual',
                    signal: controller.signal,
                }),
                controller.signal,
            );
            if (response.url) {
                try {
                    validateDiscoveryUrl(response.url);
                }
                catch (err) {
                    await response.body?.cancel().catch(() => {});
                    throw err;
                }
            }
            if (REDIRECT_STATUSES.has(response.status)) {
                await response.body?.cancel().catch(() => {});
                const location = response.headers?.get?.('location');
                if (!location) {
                    throw new Error(`HTTP ${response.status} redirect without Location for ${currentUrl}`);
                }
                if (redirects >= maxRedirects) {
                    throw new Error(`Too many redirects while fetching ${url}`);
                }
                currentUrl = validateDiscoveryUrl(new URL(location, currentUrl)).toString();
                redirects += 1;
                continue;
            }
            if (!response.ok) {
                let body = '';
                try {
                    body = await readResponseTextBounded(
                        response,
                        512,
                        controller.signal,
                        { truncateOnLimit: true },
                    );
                }
                catch (err) {
                    if (controller.signal.aborted || err?.name === 'AbortError') {
                        throw err;
                    }
                }
                throw new Error(`HTTP ${response.status} for ${currentUrl}: ${body.slice(0, 120)}`);
            }
            return await readResponseTextBounded(response, maxBytes, controller.signal);
        }
    }
    finally {
        if (timeoutId !== null) {
            clearTimeout(timeoutId);
        }
        if (callerSignal) {
            callerSignal.removeEventListener('abort', onCallerAbort);
        }
    }
}
function resolveDefaultCachePath() {
    const override = process.env.BIRD_QUERY_IDS_CACHE;
    if (override && override.trim().length > 0) {
        return path.resolve(override.trim());
    }
    return path.join(homedir(), '.config', 'bird', DEFAULT_CACHE_FILENAME);
}
export function parseSnapshot(raw) {
    if (!raw || typeof raw !== 'object') {
        return null;
    }
    const record = raw;
    const fetchedAt = typeof record.fetchedAt === 'string' ? record.fetchedAt : null;
    const ttlMs = typeof record.ttlMs === 'number' && Number.isFinite(record.ttlMs) ? record.ttlMs : null;
    const ids = record.ids && typeof record.ids === 'object' && !Array.isArray(record.ids) ? record.ids : null;
    const discovery = record.discovery && typeof record.discovery === 'object' ? record.discovery : null;
    if (!fetchedAt || !ttlMs || !ids || !discovery) {
        return null;
    }
    const pages = Array.isArray(discovery.pages) ? discovery.pages : null;
    const bundles = Array.isArray(discovery.bundles) ? discovery.bundles : null;
    if (!pages || !bundles) {
        return null;
    }
    const normalizedIds = Object.create(null);
    for (const [key, value] of Object.entries(ids)) {
        if (FORBIDDEN_KEYS.has(key)) {
            continue;
        }
        if (typeof value === 'string') {
            const trimmed = value.trim();
            if (trimmed.length > 0 && QUERY_ID_REGEX.test(trimmed)) {
                normalizedIds[key] = trimmed;
            }
        }
    }
    return {
        fetchedAt,
        ttlMs,
        ids: normalizedIds,
        discovery: {
            pages: pages.filter((p) => typeof p === 'string'),
            bundles: bundles.filter((b) => typeof b === 'string'),
        },
    };
}
async function readSnapshotFromDisk(cachePath) {
    try {
        const raw = await readFile(cachePath, 'utf8');
        return parseSnapshot(JSON.parse(raw));
    }
    catch {
        return null;
    }
}
async function resolveTrustedCacheRoot(resolvedDir) {
    const rawRoots = [homedir(), tmpdir()];
    if (process.platform === 'darwin') {
        rawRoots.push('/tmp');
    }
    const candidates = [];
    for (const rawRoot of rawRoots) {
        if (!rawRoot) {
            continue;
        }
        const resolved = path.resolve(rawRoot);
        let real = resolved;
        try {
            real = await realpath(resolved);
        }
        catch {
            continue;
        }
        candidates.push({ prefix: resolved, realRoot: real });
        if (real !== resolved) {
            candidates.push({ prefix: real, realRoot: real });
        }
    }
    candidates.sort((a, b) => b.prefix.length - a.prefix.length);
    for (const { prefix, realRoot } of candidates) {
        const rel = path.relative(prefix, resolvedDir);
        if (rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel))) {
            return { root: realRoot, relativeDir: rel };
        }
    }
    const fsRoot = path.parse(resolvedDir).root;
    return { root: fsRoot, relativeDir: path.relative(fsRoot, resolvedDir) };
}
async function ensurePrivateDirectoryNoSymlinks(dir) {
    const resolvedDir = path.resolve(dir);
    const { root, relativeDir } = await resolveTrustedCacheRoot(resolvedDir);
    const rootStat = await lstat(root);
    if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) {
        throw new Error('Refusing to write cache inside symlink or non-directory');
    }
    const segments = relativeDir ? relativeDir.split(path.sep).filter(Boolean) : [];
    let current = root;
    for (const segment of segments) {
        current = path.join(current, segment);
        let st;
        try {
            st = await lstat(current);
        }
        catch (err) {
            if (err?.code !== 'ENOENT') {
                throw err;
            }
            try {
                await mkdir(current, { mode: 0o700 });
            }
            catch (mkdirErr) {
                if (mkdirErr?.code !== 'EEXIST') {
                    throw mkdirErr;
                }
            }
            st = await lstat(current);
        }
        if (st.isSymbolicLink() || !st.isDirectory()) {
            throw new Error('Refusing to write cache inside symlink or non-directory');
        }
    }
    return current;
}
export async function writeFileAtomicPrivate(targetPath, content) {
    const resolvedTarget = path.resolve(targetPath);
    const baseName = path.basename(resolvedTarget);
    if (!baseName || baseName === '.' || baseName === '..') {
        throw new Error('Invalid cache file path');
    }
    const safeDir = await ensurePrivateDirectoryNoSymlinks(path.dirname(resolvedTarget));
    const safeTargetPath = path.join(safeDir, baseName);
    try {
        const targetStat = await lstat(safeTargetPath);
        if (targetStat.isSymbolicLink() || !targetStat.isFile()) {
            throw new Error('Refusing to overwrite symlink or non-regular cache file');
        }
    }
    catch (err) {
        if (err?.code !== 'ENOENT') {
            throw err;
        }
    }
    const tempName = `.${baseName}.${process.pid}.${randomBytes(8).toString('hex')}.tmp`;
    const tempPath = path.join(safeDir, tempName);
    try {
        await writeFile(tempPath, content, { encoding: 'utf8', flag: 'wx', mode: 0o600 });
        if (process.platform !== 'win32') {
            await chmod(tempPath, 0o600).catch(() => {});
        }
        await rename(tempPath, safeTargetPath);
    }
    catch (err) {
        await unlink(tempPath).catch(() => {});
        throw err;
    }
}
async function writeSnapshotToDisk(cachePath, snapshot) {
    await writeFileAtomicPrivate(cachePath, `${JSON.stringify(snapshot, null, 2)}\n`);
}
function extractOperations(bundleContents, bundleLabel, targets, discovered) {
    for (const pattern of OPERATION_PATTERNS) {
        pattern.regex.lastIndex = 0;
        while (true) {
            const match = pattern.regex.exec(bundleContents);
            if (match === null) {
                break;
            }
            const operationName = match[pattern.operationGroup];
            const queryId = match[pattern.queryIdGroup];
            if (!operationName || !queryId) {
                continue;
            }
            if (!targets.has(operationName)) {
                continue;
            }
            if (!QUERY_ID_REGEX.test(queryId)) {
                continue;
            }
            if (discovered.has(operationName)) {
                continue;
            }
            discovered.set(operationName, { queryId, bundle: bundleLabel });
            if (discovered.size === targets.size) {
                return;
            }
        }
    }
}
async function fetchAndExtract(fetchImpl, bundleUrls, targets, opts = {}, discovered = new Map()) {
    const deadlineMs = opts.deadlineMs;
    const outerSignal = opts.signal;
    const CONCURRENCY = 6;
    for (let i = 0; i < bundleUrls.length; i += CONCURRENCY) {
        if (discovered.size === targets.size) {
            break;
        }
        if (deadlineMs !== undefined && Date.now() >= deadlineMs) {
            break;
        }
        if (outerSignal?.aborted) {
            break;
        }
        const chunk = bundleUrls.slice(i, i + CONCURRENCY);
        const batchController = new AbortController();
        const onOuterAbort = () => batchController.abort(outerSignal.reason);
        if (outerSignal) {
            if (outerSignal.aborted) {
                batchController.abort(outerSignal.reason);
            }
            else {
                outerSignal.addEventListener('abort', onOuterAbort, { once: true });
            }
        }
        const batchOpts = { ...opts, signal: batchController.signal };
        try {
            await Promise.all(chunk.map(async (url) => {
                if (discovered.size === targets.size || batchController.signal.aborted) {
                    return;
                }
                if (deadlineMs !== undefined && Date.now() >= deadlineMs) {
                    return;
                }
                const label = url.split('/').at(-1) ?? url;
                try {
                    const js = await fetchText(fetchImpl, url, batchOpts);
                    extractOperations(js, label, targets, discovered);
                    if (discovered.size === targets.size) {
                        batchController.abort(createAbortError('All target query IDs discovered'));
                    }
                }
                catch {
                    // ignore failed or batch-aborted sibling bundles
                }
            }));
        }
        finally {
            if (outerSignal) {
                outerSignal.removeEventListener('abort', onOuterAbort);
            }
        }
        if (discovered.size === targets.size) {
            break;
        }
    }
    return discovered;
}
async function discoverAndExtract(fetchImpl, targets, opts = {}) {
    const maxBundles = opts.maxBundles ?? DEFAULT_MAX_BUNDLES;
    const deadlineMs = opts.deadlineMs;
    const seenBundles = new Set();
    const scannedPages = [];
    const discovered = new Map();
    for (const page of DISCOVERY_PAGES) {
        if (discovered.size === targets.size) {
            break;
        }
        if (seenBundles.size >= maxBundles) {
            break;
        }
        if (deadlineMs !== undefined && Date.now() >= deadlineMs) {
            break;
        }
        scannedPages.push(page);
        const newBundles = [];
        try {
            const html = await fetchText(fetchImpl, page, opts);
            for (const match of html.matchAll(BUNDLE_URL_REGEX)) {
                const bundleUrl = match[0];
                if (seenBundles.has(bundleUrl)) {
                    continue;
                }
                seenBundles.add(bundleUrl);
                newBundles.push(bundleUrl);
                if (seenBundles.size >= maxBundles) {
                    break;
                }
            }
        }
        catch {
            // ignore discovery page failures; other pages often work
            continue;
        }
        if (newBundles.length > 0) {
            await fetchAndExtract(fetchImpl, newBundles, targets, opts, discovered);
        }
    }
    const bundleUrls = [...seenBundles];
    if (bundleUrls.length === 0) {
        throw new Error('No client bundles discovered; x.com layout may have changed.');
    }
    return { bundleUrls, discovered, scannedPages };
}
export function createRuntimeQueryIdStore(options = {}) {
    const fetchImpl = options.fetchImpl ?? fetch;
    const ttlMs = options.ttlMs ?? DEFAULT_TTL_MS;
    const defaultFetchTimeoutMs = options.fetchTimeoutMs ?? DEFAULT_FETCH_TIMEOUT_MS;
    const defaultRefreshTimeoutMs = options.refreshTimeoutMs ?? DEFAULT_REFRESH_TIMEOUT_MS;
    const defaultMaxBytes = options.maxBytes ?? DEFAULT_MAX_RESPONSE_BYTES;
    const defaultMaxBundles = options.maxBundles ?? DEFAULT_MAX_BUNDLES;
    const defaultMaxRedirects = options.maxRedirects ?? DEFAULT_MAX_REDIRECTS;
    const cachePath = options.cachePath ? path.resolve(options.cachePath) : resolveDefaultCachePath();
    let memorySnapshot = null;
    let loadOnce = null;
    let refreshInFlight = null;
    const loadSnapshot = async () => {
        if (memorySnapshot) {
            return memorySnapshot;
        }
        if (!loadOnce) {
            loadOnce = (async () => {
                const fromDisk = await readSnapshotFromDisk(cachePath);
                memorySnapshot = fromDisk;
                return fromDisk;
            })();
        }
        return loadOnce;
    };
    const getSnapshotInfo = async () => {
        const snapshot = await loadSnapshot();
        if (!snapshot) {
            return null;
        }
        const fetchedAtMs = new Date(snapshot.fetchedAt).getTime();
        const ageMs = Number.isFinite(fetchedAtMs) ? Math.max(0, Date.now() - fetchedAtMs) : Number.POSITIVE_INFINITY;
        const effectiveTtl = Number.isFinite(snapshot.ttlMs) ? snapshot.ttlMs : ttlMs;
        const isFresh = ageMs <= effectiveTtl;
        return { snapshot, cachePath, ageMs, isFresh };
    };
    const getQueryId = async (operationName) => {
        if (typeof operationName !== 'string' || FORBIDDEN_KEYS.has(operationName)) {
            return null;
        }
        const info = await getSnapshotInfo();
        if (!info) {
            return null;
        }
        const value = info.snapshot.ids[operationName];
        return typeof value === 'string' && QUERY_ID_REGEX.test(value) ? value : null;
    };
    const refresh = async (operationNames, opts = {}) => {
        if (refreshInFlight) {
            return refreshInFlight;
        }
        refreshInFlight = (async () => {
            const current = await getSnapshotInfo();
            if (!opts.force && current?.isFresh) {
                return current;
            }
            const refreshTimeoutMs = opts.refreshTimeoutMs ?? defaultRefreshTimeoutMs;
            const fetchOpts = {
                fetchTimeoutMs: opts.fetchTimeoutMs ?? defaultFetchTimeoutMs,
                maxBytes: opts.maxBytes ?? defaultMaxBytes,
                maxBundles: opts.maxBundles ?? defaultMaxBundles,
                maxRedirects: opts.maxRedirects ?? defaultMaxRedirects,
                deadlineMs: Date.now() + refreshTimeoutMs,
                signal: opts.signal,
            };
            const targets = new Set(operationNames);
            const { bundleUrls, discovered, scannedPages } = await discoverAndExtract(
                fetchImpl,
                targets,
                fetchOpts,
            );
            if (discovered.size === 0) {
                return current ?? null;
            }
            const ids = Object.create(null);
            for (const name of operationNames) {
                if (FORBIDDEN_KEYS.has(name)) {
                    continue;
                }
                const entry = discovered.get(name);
                if (entry?.queryId && QUERY_ID_REGEX.test(entry.queryId)) {
                    ids[name] = entry.queryId;
                }
            }
            const snapshot = {
                fetchedAt: new Date().toISOString(),
                ttlMs,
                ids,
                discovery: {
                    pages: scannedPages,
                    bundles: bundleUrls.map((url) => url.split('/').at(-1) ?? url),
                },
            };
            memorySnapshot = snapshot;
            try {
                await writeSnapshotToDisk(cachePath, snapshot);
            }
            catch {
                // Keep the refreshed snapshot in memory on read-only filesystems
                // or unwritable cache paths.
            }
            return getSnapshotInfo();
        })().finally(() => {
            refreshInFlight = null;
        });
        return refreshInFlight;
    };
    return {
        cachePath,
        ttlMs,
        getSnapshotInfo,
        getQueryId,
        refresh,
        clearMemory() {
            memorySnapshot = null;
            loadOnce = null;
        },
    };
}
export const runtimeQueryIds = createRuntimeQueryIdStore();
//# sourceMappingURL=runtime-query-ids.js.map