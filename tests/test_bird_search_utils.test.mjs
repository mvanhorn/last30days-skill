import test from 'node:test';
import assert from 'node:assert/strict';

import {
  collectTweetResultsFromEntry,
  extractArticleText,
  extractCursorFromInstructions,
  extractMedia,
  findTweetInInstructions,
  mapTweetResult,
  parseTweetsFromInstructions,
  parseUsersFromInstructions,
  renderContentState,
  unwrapTweetResult,
} from '../skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-utils.js';
import { withSearch } from '../skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-search.js';

function makeRawTweet(id, username, text, extra = {}) {
  return {
    __typename: 'Tweet',
    rest_id: id,
    core: {
      user_results: {
        result: {
          rest_id: `u_${id}`,
          legacy: {
            screen_name: username,
            name: username,
          },
        },
      },
    },
    legacy: {
      full_text: text,
      created_at: 'Wed Aug 12 15:55:18 +0000 2026',
      favorite_count: 10,
      retweet_count: 2,
      reply_count: 1,
      conversation_id_str: id,
    },
    ...extra,
  };
}

test('HIGH-162: collectTweetResultsFromEntry and parseTweetsFromInstructions unwrap TweetWithVisibilityResults', () => {
  const directWrapped = {
    __typename: 'TweetWithVisibilityResults',
    tweet: makeRawTweet('101', 'alice', 'visibility-wrapped direct tweet'),
  };
  const moduleWrapped = {
    __typename: 'TweetWithVisibilityResults',
    tweet: makeRawTweet('102', 'bob', 'visibility-wrapped module tweet'),
  };
  const nestedWrapped = {
    __typename: 'TweetWithVisibilityResults',
    tweet: makeRawTweet('103', 'carol', 'visibility-wrapped item tweet'),
  };

  const entryDirect = {
    content: {
      itemContent: {
        tweet_results: { result: directWrapped },
      },
    },
  };
  const entryModule = {
    content: {
      items: [
        { item: { itemContent: { tweet_results: { result: moduleWrapped } } } },
        { content: { itemContent: { tweet_results: { result: nestedWrapped } } } },
      ],
    },
  };

  assert.deepEqual(
    collectTweetResultsFromEntry(entryDirect).map((t) => t.rest_id),
    ['101'],
  );
  assert.deepEqual(
    collectTweetResultsFromEntry(entryModule).map((t) => t.rest_id),
    ['102', '103'],
  );

  const instructions = [
    {
      type: 'TimelineAddEntries',
      entries: [entryDirect, entryModule],
    },
  ];
  const parsed = parseTweetsFromInstructions(instructions, { quoteDepth: 1 });
  assert.deepEqual(
    parsed.map((t) => ({ id: t.id, user: t.author.username, text: t.text })),
    [
      { id: '101', user: 'alice', text: 'visibility-wrapped direct tweet' },
      { id: '102', user: 'bob', text: 'visibility-wrapped module tweet' },
      { id: '103', user: 'carol', text: 'visibility-wrapped item tweet' },
    ],
  );

  const found = findTweetInInstructions(instructions, '101');
  assert.equal(found?.rest_id, '101');
  assert.equal(unwrapTweetResult(null), undefined);
  assert.equal(unwrapTweetResult('bad'), undefined);
});

test('HIGH-162: extractCursorFromInstructions supports TimelineReplaceEntry singular instruction.entry', () => {
  const instructions = [
    null,
    { type: 'TimelineAddEntries', entries: [null, { content: { entryType: 'TimelineTimelineItem' } }] },
    {
      type: 'TimelineReplaceEntry',
      entry_id_to_replace: 'cursor-bottom-0',
      entry: {
        entryId: 'cursor-bottom-0',
        content: {
          entryType: 'TimelineTimelineCursor',
          cursorType: 'Bottom',
          value: 'CURSOR_PAGE_2',
        },
      },
    },
  ];
  assert.equal(extractCursorFromInstructions(instructions), 'CURSOR_PAGE_2');
  assert.equal(extractCursorFromInstructions(instructions, 'Top'), undefined);
  assert.equal(extractCursorFromInstructions(null), undefined);
  assert.equal(extractCursorFromInstructions('not-array'), undefined);
});

test('HIGH-162: renderContentState handles malformed blocks, entityMap, link ranges, and atomic blocks without TypeError', () => {
  assert.equal(renderContentState(null), undefined);
  assert.equal(renderContentState({ blocks: 'not-an-array' }), undefined);
  assert.equal(renderContentState({ blocks: [null, undefined, 42] }), undefined);

  const rendered = renderContentState({
    entityMap: [
      null,
      { key: '0', value: { type: 'LINK' } }, // missing data
      { key: '1', value: { type: 'LINK', data: { url: 'https://example.com' } } },
      { key: '2', value: { type: 'MARKDOWN' } }, // missing data
      { key: '3', value: { type: 'MARKDOWN', data: { markdown: 12345 } } }, // non-string markdown
      { key: '4', value: { type: 'TWEET' } }, // missing data
      { key: '5', value: { type: 'LINK' } }, // atomic LINK missing data
      { key: '6', value: { type: 'MARKDOWN', data: { markdown: '  ```js\n1+1\n```  ' } } },
      { key: '7', value: { type: 'TWEET', data: { tweetId: '999' } } },
      { key: '8', value: { type: 'LINK', data: { url: 'https://x.com/test' } } },
    ],
    blocks: [
      null,
      { type: 'unstyled', text: undefined, entityRanges: [null, { key: 0, offset: 0, length: 4 }] },
      { type: 'unstyled', text: null, entityRanges: 'bad' },
      {
        type: 'unstyled',
        text: 'Read docs and broken',
        entityRanges: [
          null,
          { key: 0, offset: 14, length: 6 }, // missing entity.data
          { key: 1, offset: 5, length: 4 }, // valid LINK
          { key: 1, offset: 'bad', length: 4 }, // non-numeric offset
          { key: 1, offset: -2, length: 4 }, // negative offset
          { key: 1, offset: 0, length: -1 }, // negative length
          { key: 1, offset: Number.NaN, length: 4 }, // NaN offset
          { key: 1, offset: 0, length: Number.POSITIVE_INFINITY }, // Infinity length
          { key: 1, offset: 1.5, length: 2 }, // non-integer offset
        ],
      },
      { type: 'atomic', entityRanges: [null] },
      { type: 'atomic', entityRanges: [{ key: 2 }] },
      { type: 'atomic', entityRanges: [{ key: 3 }] },
      { type: 'atomic', entityRanges: [{ key: 4 }] },
      { type: 'atomic', entityRanges: [{ key: 5 }] },
      { type: 'atomic', entityRanges: [{ key: 6 }] },
      { type: 'atomic', entityRanges: [{ key: 7 }] },
      { type: 'atomic', entityRanges: [{ key: 8 }] },
    ],
  });

  assert.equal(
    rendered,
    [
      'Read [docs](https://example.com) and broken',
      '```js\n1+1\n```',
      '[Embedded Tweet: https://x.com/i/status/999]',
      '[Link: https://x.com/test]',
    ].join('\n\n'),
  );

  // Primitive entityMap should not throw
  assert.equal(
    renderContentState({ entityMap: 123, blocks: [{ type: 'unstyled', text: 'plain' }] }),
    'plain',
  );
});

test('HIGH-162: extractMedia handles non-array media/variants and null elements without TypeError', () => {
  assert.equal(extractMedia({ legacy: { extended_entities: { media: 'not-array' } } }), undefined);
  assert.equal(extractMedia({ legacy: { extended_entities: { media: [null, {}] } } }), undefined);

  const tweet = {
    legacy: {
      extended_entities: {
        media: [
          null,
          {
            type: 'video',
            media_url_https: 'https://pbs.twimg.com/thumb.jpg',
            video_info: { variants: 'not-an-array' },
          },
          {
            type: 'video',
            media_url_https: 'https://pbs.twimg.com/thumb2.jpg',
            video_info: {
              duration_millis: 5000,
              variants: [
                null,
                { content_type: 'application/x-mpegURL', url: 'https://video.twimg.com/p.m3u8' },
                { content_type: 'video/mp4', bitrate: 832000, url: 'https://video.twimg.com/hi.mp4' },
                { content_type: 'video/mp4', bitrate: 256000, url: 'https://video.twimg.com/lo.mp4' },
              ],
            },
          },
        ],
      },
    },
  };

  const media = extractMedia(tweet);
  assert.equal(media?.length, 2);
  assert.equal(media[0].videoUrl, undefined);
  assert.equal(media[1].videoUrl, 'https://video.twimg.com/hi.mp4');
  assert.equal(media[1].durationMs, 5000);
});

test('HIGH-162: parseTweetsFromInstructions isolates throwing/malformed tweets and keeps valid siblings', () => {
  const throwingTweet = {
    rest_id: '201',
    get core() {
      throw new TypeError('unexpected getter crash');
    },
  };
  const badArticleTweet = makeRawTweet('202', 'alice', 'fallback text', {
    article: {
      article_results: {
        result: {
          content_state: {
            blocks: [{ type: 'unstyled', text: null }, { type: 'atomic', entityRanges: [null] }],
            entityMap: [null],
          },
        },
      },
    },
  });
  const goodTweet = makeRawTweet('203', 'bob', 'valid sibling survives');

  const instructions = [
    null,
    'not-an-object',
    {
      type: 'TimelineAddEntries',
      entries: [
        null,
        { content: { items: 'not-an-array' } },
        { content: { itemContent: { tweet_results: { result: throwingTweet } } } },
        { content: { itemContent: { tweet_results: { result: badArticleTweet } } } },
        { content: { itemContent: { tweet_results: { result: goodTweet } } } },
      ],
    },
  ];

  const tweets = parseTweetsFromInstructions(instructions, { quoteDepth: 1 });
  assert.deepEqual(
    tweets.map((t) => t.id),
    ['202', '203'],
  );
  assert.equal(tweets[0].text, 'fallback text');
  assert.equal(tweets[1].text, 'valid sibling survives');
  assert.equal(extractArticleText({ article: 'bad' }), undefined);
  assert.equal(mapTweetResult(goodTweet)?.id, '203');
  assert.deepEqual(parseUsersFromInstructions([null, { entries: 'bad' }]), []);
});

test('HIGH-162: searchPaged follows TimelineReplaceEntry cursor and preserves TweetWithVisibilityResults across pages', async () => {
  class DummyBase {
    constructor() {
      this.quoteDepth = 1;
      this.timeoutMs = 5000;
    }
    async getSearchTimelineQueryIds() {
      return ['ValidQueryId123'];
    }
    getHeaders() {
      return {};
    }
    async refreshQueryIds() {}
    async fetchTextWithTimeout(url) {
      const parsed = new URL(url);
      const vars = JSON.parse(parsed.searchParams.get('variables'));
      if (!vars.cursor) {
        return {
          status: 200,
          ok: true,
          text: JSON.stringify({
            data: {
              search_by_raw_query: {
                search_timeline: {
                  timeline: {
                    instructions: [
                      {
                        type: 'TimelineAddEntries',
                        entries: [
                          {
                            content: {
                              itemContent: {
                                tweet_results: {
                                  result: {
                                    __typename: 'TweetWithVisibilityResults',
                                    tweet: makeRawTweet('301', 'alice', 'page 1 visibility tweet'),
                                  },
                                },
                              },
                            },
                          },
                        ],
                      },
                      {
                        type: 'TimelineReplaceEntry',
                        entry: {
                          content: {
                            cursorType: 'Bottom',
                            value: 'REPLACE_CURSOR_2',
                          },
                        },
                      },
                    ],
                  },
                },
              },
            },
          }),
        };
      }
      assert.equal(vars.cursor, 'REPLACE_CURSOR_2');
      return {
        status: 200,
        ok: true,
        text: JSON.stringify({
          data: {
            search_by_raw_query: {
              search_timeline: {
                timeline: {
                  instructions: [
                    {
                      type: 'TimelineAddEntries',
                      entries: [
                        {
                          content: {
                            itemContent: {
                              tweet_results: {
                                result: makeRawTweet('302', 'bob', 'page 2 tweet'),
                              },
                            },
                          },
                        },
                      ],
                    },
                  ],
                },
              },
            },
          },
        }),
      };
    }
  }

  const SearchClient = withSearch(DummyBase);
  const client = new SearchClient();
  const res = await client.search('test', 25, { maxPages: 3 });
  assert.equal(res.success, true);
  assert.deepEqual(
    res.tweets.map((t) => t.id),
    ['301', '302'],
  );
});
