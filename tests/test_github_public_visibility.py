from urllib.parse import parse_qs, urlsplit

import pytest

from lib import github


START, END = "2026-03-01", "2026-03-31"
TOKEN = "dummy-github-token"


@pytest.mark.parametrize("username", ["alice", "alice_org"])
def test_person_partition_queries_are_public_and_keep_auth(monkeypatch, username):
    queries = []

    def fetch(url, *, token, timeout):
        path = urlsplit(url).path
        if path == "/search/issues":
            query = parse_qs(urlsplit(url).query)["q"][0]
            queries.append(query)
            assert token == TOKEN
            return {"total_count": 0 if query.endswith(" is:open") else 1, "items": []}
        assert path == f"/users/{username}/repos"
        return []

    monkeypatch.setattr(github, "_fetch_json", fetch)

    results = github.search_github_person(username, START, END, token=TOKEN)

    base = f"is:public author:{username} type:pr created:{START}..{END}"
    assert queries == [base, f"{base} is:merged", f"{base} is:open"]
    assert results[0]["engagement"]["total_prs"] == 1


def test_public_project_keeps_authenticated_metadata_and_public_release(monkeypatch):
    calls = []

    def fetch(url, *, token=None, timeout):
        parsed = urlsplit(url)
        if parsed.path == "/repos/alice/project":
            assert token == TOKEN
            return {"visibility": "public", "stargazers_count": 42}
        if parsed.path == "/repos/alice/project/releases":
            assert token is None
            return [{"tag_name": "v1", "published_at": "2026-03-14T10:00:00Z"}]
        assert parsed.path == "/search/issues"
        assert token == TOKEN
        calls.append(parse_qs(parsed.query)["q"][0])
        return {"total_count": 0, "items": []}

    monkeypatch.setattr(github, "_fetch_json", fetch)
    monkeypatch.setattr(github, "_fetch_readme_snippet", lambda repo, *, max_chars: "Public README")

    results = github.search_github_project(["alice/project"], START, END, token=TOKEN)

    assert len(results) == 1
    assert results[0]["engagement"]["stars"] == 42
    assert results[0]["date"] == "2026-03-14"
    assert "Public README" in results[0]["snippet"]
    assert "Latest release: v1" in results[0]["snippet"]
    assert calls == [
        "is:public repo:alice/project is:issue is:open label:enhancement",
        "is:public repo:alice/project is:issue is:open",
        "is:public repo:alice/project is:issue is:open",
    ]


@pytest.mark.parametrize("metadata", [
    {"visibility": "private", "private": True},
    {"visibility": "internal"},
    {"private": False},
])
def test_project_without_confirmed_public_metadata_skips_enrichment(monkeypatch, metadata):
    urls = []

    def fetch(url, *, token=None, timeout):
        urls.append(url)
        return metadata

    def unexpected_readme(*args, **kwargs):
        pytest.fail("README fetch requires confirmed public metadata")

    monkeypatch.setattr(github, "_fetch_json", fetch)
    monkeypatch.setattr(github, "_fetch_readme_snippet", unexpected_readme)

    assert github.search_github_project(["alice/project"], START, END, token=TOKEN) == []
    assert urls == ["https://api.github.com/repos/alice/project"]
