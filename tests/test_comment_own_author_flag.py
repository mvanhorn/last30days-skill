"""A stored comment says when its author is the item's own author.

A YouTube channel's display name ("Engineering Explained") and the handle its
comments carry ("@EngExplained") can differ, so comparing names cannot always
tell a creator's pinned note from a community reaction. Where a source says so
itself, the fetcher stores ``is_uploader`` on the comment and the quote pool
reads it before falling back to names. A comment without the key is unknown.
"""

import json
from unittest import mock

from lib import instagram, normalize, reddit_enrich, render, schema, tiktok, youtube_yt
from lib.subproc import SubprocResult

FROM_DATE, TO_DATE = "2026-10-11", "2026-11-10"


def _ytdlp(comments, **video):
    payload = {"id": "abc123", "title": "vid", "comments": comments, **video}
    result = SubprocResult(returncode=0, stdout=json.dumps(payload), stderr="")
    with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
         mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=result):
        return youtube_yt._fetch_video_comments_ytdlp("abc123", max_comments=5)


def test_ytdlp_comment_keeps_the_uploader_flag_it_carries():
    got = _ytdlp([
        {"author": "@EngExplained", "author_id": "UC1", "author_is_uploader": True,
         "text": "Addressing technical questions!", "like_count": 683},
        {"author": "@viewer", "author_id": "UC2", "author_is_uploader": False,
         "text": "Great breakdown of the gearbox", "like_count": 120},
    ])
    assert [c["is_uploader"] for c in got] == [True, False]


def test_ytdlp_comment_falls_back_to_the_channel_id_when_it_has_no_flag():
    got = _ytdlp(
        [
            {"author": "@EngExplained", "author_id": "UC1", "text": "A pinned note", "like_count": 9},
            {"author": "@viewer", "author_id": "UC2", "text": "A viewer reaction", "like_count": 8},
            {"author": "@nobody", "text": "No id on this one", "like_count": 7},
        ],
        channel_id="UC1",
    )
    assert [c.get("is_uploader") for c in got] == [True, False, None]
    assert "is_uploader" not in got[2]


def test_ytdlp_comment_without_any_identity_has_no_flag():
    (got,) = _ytdlp([{"author": "@BestFlorin", "text": "Trump said the strait is open", "like_count": 11,
                      "_time_text": "2 days ago"}])
    assert got == {"author": "@BestFlorin", "text": "Trump said the strait is open", "likes": 11,
                   "date": "2 days ago"}


def test_scrapecreators_youtube_comment_keeps_a_creator_flag_when_one_is_sent():
    sc_payload = {"comments": [
        {"text": "from the channel", "author": {"name": "@x", "isCreator": True}, "likes": 3},
        {"text": "from a viewer", "author": {"name": "@y", "isCreator": False}, "likes": 2},
        {"text": "unknown", "author": {"name": "@z"}, "likes": 1},
    ]}
    with mock.patch.object(youtube_yt, "_ytdlp_comments_result", return_value=([], False)), \
         mock.patch.object(youtube_yt.http, "get", return_value=sc_payload):
        got = youtube_yt._fetch_video_comments("abc123", token="sk-live", max_comments=5)
    assert [c.get("is_uploader") for c in got] == [True, False, None]


def test_tiktok_comments_are_flagged_against_the_videos_creator_handle():
    fake = {"comments": [
        {"text": "thanks for watching everyone", "digg_count": 900, "user": {"unique_id": "YeniusWest"}},
        {"text": "actually great quote", "digg_count": 500, "user": {"unique_id": "markosoo3"}},
        {"text": "no handle on this comment", "digg_count": 100, "user": {"nickname": "Display Name"}},
    ]}
    item = {"url": "https://www.tiktok.com/@yeniuswest/video/1", "author_name": "yeniuswest",
            "engagement": {"views": 10}}
    with mock.patch.object(tiktok.http, "get", return_value=fake):
        tiktok.enrich_with_comments([item], token="k")
    assert [c.get("is_uploader") for c in item["top_comments"]] == [True, False, None]


def test_instagram_comments_are_flagged_against_the_posts_owner():
    fake = {"comments": [
        {"text": "appreciate you all", "comment_like_count": 500, "user": {"username": "IamKevinWong"}},
        {"text": "he really walked out", "comment_like_count": 40, "user": {"username": "glovejones"}},
    ]}
    item = {"url": "https://www.instagram.com/reel/ABC/", "author_name": "iamkevinwong",
            "engagement": {"views": 10}}
    with mock.patch.object(instagram.http, "get", return_value=fake):
        instagram.enrich_with_comments([item], token="k")
    assert [c.get("is_uploader") for c in item["top_comments"]] == [True, False]


def _thread(comments):
    return [
        {"data": {"children": [{"kind": "t3", "data": {
            "score": 10, "num_comments": 2, "upvote_ratio": 0.9, "created_utc": 1790000000,
            "permalink": "/r/sub/comments/abc/t/", "title": "t", "selftext": ""}}]}},
        {"data": {"children": [{"kind": "t1", "data": c} for c in comments]}},
    ]


def test_reddit_thread_comment_keeps_is_submitter():
    parsed = reddit_enrich.parse_thread_data(_thread([
        {"body": "OP here, thanks all", "author": "thread_op", "score": 50, "is_submitter": True,
         "permalink": "/r/sub/comments/abc/t/c1/"},
        {"body": "A reply from the crowd", "author": "replier", "score": 40, "is_submitter": False,
         "permalink": "/r/sub/comments/abc/t/c2/"},
        {"body": "An older capture with no field", "author": "old", "score": 30,
         "permalink": "/r/sub/comments/abc/t/c3/"},
    ]))
    assert [c.get("is_submitter") for c in parsed["comments"]] == [True, False, None]
    stored = reddit_enrich.stored_top_comments(parsed["comments"])
    assert [c.get("is_uploader") for c in stored] == [True, False, None]
    assert "is_uploader" not in stored[2]


def test_normalize_carries_the_flag_and_leaves_unknown_out():
    item = normalize._normalize_youtube("youtube", {
        "id": "v1", "title": "A video", "url": "https://www.youtube.com/watch?v=v1",
        "channel_name": "Engineering Explained", "date": "2026-11-01",
        "top_comments": [
            {"author": "@EngExplained", "text": "A pinned note", "likes": 9, "is_uploader": True},
            {"author": "@viewer", "text": "A viewer reaction", "likes": 8, "is_uploader": False},
            {"author": "@old", "text": "Stored before the flag", "likes": 7},
        ],
    }, 0, FROM_DATE, TO_DATE)
    stored = item.metadata["top_comments"]
    assert [c.get("is_uploader") for c in stored] == [True, False, None]
    assert "is_uploader" not in stored[2]


def _pool_item(comments, author="Engineering Explained"):
    item = schema.SourceItem(
        item_id="v1", source="youtube", title="A video", body="", author=author,
        url="https://www.youtube.com/watch?v=v1", metadata={"top_comments": comments},
    )
    return item


def test_pool_trusts_the_flag_before_the_names():
    item = _pool_item([])
    flagged = {"author": "@EngExplained", "score": 683, "excerpt": "x", "is_uploader": True}
    cleared = {"author": "@EngineeringExplained", "score": 5, "excerpt": "x", "is_uploader": False}
    unknown_same = {"author": "@EngineeringExplained", "score": 5, "excerpt": "x"}
    unknown_other = {"author": "@viewer", "score": 5, "excerpt": "x"}
    assert render._is_community_comment(item, flagged, "A pinned note") is False
    # A fan account that shares the channel's name is still a viewer.
    assert render._is_community_comment(item, cleared, "A viewer reaction") is True
    assert render._is_community_comment(item, unknown_same, "A pinned note") is False
    assert render._is_community_comment(item, unknown_other, "A viewer reaction") is True
