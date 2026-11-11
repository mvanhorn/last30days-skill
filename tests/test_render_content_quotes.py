"""The quote pool holds what was said in videos, not only the comments on them.

A YouTube transcript highlight or a reel's spoken line joins the pool as a
``said`` entry with the video's like count, ready to copy as a quote line. A
small on-topic Reddit thread joins with a few votes on its top comment.
"""

from lib import render, schema


def _item(source, url, *, author="", comments=None, likes=None, snippet="",
          highlights=None, container=None, score=None, spoken=False):
    metadata = {}
    if spoken:
        metadata["spoken_caption"] = True
    if comments:
        metadata["top_comments"] = comments
    if highlights:
        metadata["transcript_highlights"] = highlights
    engagement = {}
    if likes is not None:
        engagement["likes"] = likes
    if score is not None:
        engagement["score"] = score
    return schema.SourceItem(
        item_id=f"i-{url}",
        source=source,
        title="Post",
        body=snippet,
        url=url,
        author=author,
        container=container,
        snippet=snippet,
        engagement=engagement,
        metadata=metadata,
    )


def _candidate(item):
    candidate = schema.Candidate(
        candidate_id=f"c-{item.url}",
        item_id=item.item_id,
        source=item.source,
        title=item.title,
        url=item.url,
        snippet="",
        subquery_labels=["q1"],
        native_ranks={item.source: 1},
        local_relevance=0.8,
        freshness=50,
        engagement=0,
        source_quality=0.5,
        rrf_score=0.01,
        source_items=[item],
    )
    candidate.final_score = 50.0
    return candidate


def _report(*items):
    return schema.Report(
        topic="Sequoia Capital",
        range_from="2026-10-12",
        range_to="2026-11-11",
        generated_at="2026-11-11T00:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="test", planner_model="test", rerank_model="test"
        ),
        query_plan=schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="Sequoia Capital",
            subqueries=[],
            source_weights={},
        ),
        ranked_candidates=[_candidate(item) for item in items],
        clusters=[],
        items_by_source={},
        errors_by_source={},
    )


WSJ = _item(
    "youtube",
    "https://www.youtube.com/watch?v=Gmp1_uPoIS0",
    author="WSJ Events",
    likes=386,
    highlights=[
        "&gt;&gt; Given the results of the midterms, do you think the venture "
        "capital industry has tied itself too close to Trump?",
    ],
)
REEL = _item(
    "instagram",
    "https://www.instagram.com/reel/DfYRA2ghafT/",
    author="atlasberry008",
    likes=1943,
    snippet=(
        "Let me get this straight. Sequoia just raised $10 billion and in the same "
        "letter to announce it, they shut down their London office, their scout "
        "program and their accelerator program, the most famous VC fund in the world "
        "Marie Kondoed itself, and when the best venture firm in venture starts "
        "cutting, it's telling you about the new world order they're getting ready for."
    ),
    comments=[{"author": "@fan", "score": 0, "excerpt": "Sequoia"}],
    spoken=True,
)
NEOLIBERAL = _item(
    "reddit",
    "https://www.reddit.com/r/neoliberal/comments/1xntipt/sequoia_shuts_london/",
    container="neoliberal",
    score=23,
    comments=[
        {"author": "ldn6", "score": 4,
         "excerpt": "This is definitely an outlying move because London is recording a surge in AI investment."},
    ],
)


def _lines(*items):
    return render._render_top_comments(_report(*items))


def test_youtube_highlight_joins_the_pool_as_said_with_likes():
    lines = _lines(WSJ, NEOLIBERAL)
    (line,) = [line for line in lines if "midterms" in line]
    assert line.startswith('- "Given the results of the midterms')
    assert "&gt;" not in line and ">>" not in line
    assert "🔴 WSJ Events on YouTube · 386 likes" in line
    assert line.endswith("— said")


def test_reel_spoken_caption_joins_the_pool_and_its_bait_comments_do_not():
    lines = _lines(REEL, NEOLIBERAL)
    said = [line for line in lines if "Marie Kondoed" in line]
    assert len(said) == 1
    assert "atlasberry008 on Instagram · 1,943 likes" in said[0]
    assert not any(line.startswith('- "Sequoia"') for line in lines)


def test_a_video_under_the_like_floor_says_nothing():
    quiet = _item(
        "youtube", "https://www.youtube.com/watch?v=quiet", author="Small",
        likes=3, highlights=["Sequoia Capital raised ten billion dollars this week in new funds."],
    )
    lines = _lines(quiet, NEOLIBERAL)
    assert not any("ten billion" in line for line in lines)


def test_small_reddit_thread_joins_with_a_four_vote_top_comment():
    lines = _lines(NEOLIBERAL, WSJ)
    assert any("outlying move" in line and "top comment" in line for line in lines)


def test_large_reddit_thread_still_needs_five_votes():
    big = _item(
        "reddit", "https://www.reddit.com/r/startups/comments/big/thread/",
        container="startups", score=900,
        comments=[{"author": "a", "score": 4, "excerpt": "Four votes on a huge thread is noise."}],
    )
    lines = _lines(big, WSJ)
    assert not any("huge thread" in line for line in lines)


def test_linkedin_post_with_placeholder_author_is_skipped():
    placeholder = _item(
        "linkedin", "https://www.linkedin.com/posts/x", author="LinkedIn", likes=87,
        snippet="Sequoia just ended its scouts program and the firm that made scouting famous shut it down.",
    )
    named = _item(
        "linkedin", "https://www.linkedin.com/posts/y", author="Jane Doe", likes=231,
        snippet="A week has passed since the news about Sequoia Capital's European office closing.",
    )
    lines = _lines(placeholder, named, WSJ)
    assert not any("scouts program" in line for line in lines)
    assert any("European office" in line and line.endswith("— post") for line in lines)


def test_a_video_gives_its_top_comment_before_what_it_said():
    video = _item(
        "youtube", "https://www.youtube.com/watch?v=both", author="WSJ Events",
        likes=386,
        highlights=["Sequoia Capital raised ten billion dollars and closed its London office."],
        comments=[{"author": "viewer", "score": 120, "excerpt": "Bigger is not better, apparently."}],
    )
    lines = [line for line in _lines(video, NEOLIBERAL) if line.startswith("- ")]
    video_labels = [line.rsplit("— ", 1)[1] for line in lines if "watch?v=both" in line]
    assert video_labels == ["top comment", "said"]


def test_a_reel_without_a_transcript_says_nothing():
    described = _item(
        "instagram", "https://www.instagram.com/reel/written/", author="writer",
        likes=500,
        snippet="Sequoia Capital closed its London office and ended the scout program this month, link in bio.",
    )
    lines = _lines(described, NEOLIBERAL)
    assert not any("link in bio" in line for line in lines)


def test_one_long_spoken_sentence_stays_whole():
    sentence = (
        "Sequoia just raised $10 billion and in the same letter to announce it they "
        "shut down their London office and their scout program and their accelerator "
        "program because the most famous VC fund in the world Marie Kondoed itself "
        "and the best venture firm is telling you about the new world order it sees"
    )
    assert 50 < len(sentence.split()) <= 70
    reel = _item(
        "tiktok", "https://www.tiktok.com/@x/video/1", author="x", likes=900,
        snippet=sentence + ".", spoken=True,
    )
    assert render._content_quotes(reel, "Sequoia Capital") == [sentence + "."]


def test_normalize_marks_only_transcripts_as_spoken():
    from lib import normalize

    base = {"video_id": "1", "url": "https://www.tiktok.com/@x/video/1", "text": "desc"}
    written = normalize._normalize_shortform_video(
        "tiktok", {**base, "caption_snippet": "desc"}, 0, "2026-10-12", "2026-11-11", "tk", "TikTok"
    )
    spoken = normalize._normalize_shortform_video(
        "tiktok", {**base, "caption_snippet": "words said", "caption_is_transcript": True},
        0, "2026-10-12", "2026-11-11", "tk", "TikTok",
    )
    assert written.metadata["spoken_caption"] is False
    assert spoken.metadata["spoken_caption"] is True
