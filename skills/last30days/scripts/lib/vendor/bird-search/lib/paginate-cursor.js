export async function paginateCursor(opts) {
    const { maxPages, pageDelayMs = 1000 } = opts;
    let cursor = opts.cursor;
    if (maxPages !== undefined) {
        if (typeof maxPages !== 'number' || !Number.isFinite(maxPages)) {
            return { success: false, error: 'Invalid maxPages' };
        }
        if (maxPages <= 0) {
            return { success: true, items: [], nextCursor: cursor };
        }
    }
    const effectiveMaxPages = maxPages !== undefined ? Math.floor(maxPages) : undefined;
    if (effectiveMaxPages !== undefined && effectiveMaxPages <= 0) {
        return { success: true, items: [], nextCursor: cursor };
    }
    const seen = new Set();
    const seenCursors = new Set(cursor ? [cursor] : []);
    const items = [];
    let pagesFetched = 0;
    while (true) {
        if (pagesFetched > 0 && pageDelayMs > 0 && typeof opts.sleep === 'function') {
            await opts.sleep(pageDelayMs);
        }
        const page = await opts.fetchPage(cursor);
        if (!page.success) {
            if (items.length > 0) {
                return { success: false, error: page.error, items, nextCursor: cursor };
            }
            return page;
        }
        pagesFetched += 1;
        const pageItems = Array.isArray(page.items) ? page.items : [];
        let added = 0;
        for (const item of pageItems) {
            const key = opts.getKey(item);
            if (seen.has(key)) {
                continue;
            }
            seen.add(key);
            items.push(item);
            added += 1;
        }
        const pageCursor = page.cursor;
        if (!pageCursor || seenCursors.has(pageCursor) || pageItems.length === 0 || added === 0) {
            return { success: true, items, nextCursor: undefined };
        }
        seenCursors.add(pageCursor);
        if (effectiveMaxPages !== undefined && pagesFetched >= effectiveMaxPages) {
            return { success: true, items, nextCursor: pageCursor };
        }
        cursor = pageCursor;
    }
}
//# sourceMappingURL=paginate-cursor.js.map