"""The private file: what it matches, and that the engine refuses before sending."""

import io
import json
import sys
from unittest import mock

import pytest

import last30days as cli
from lib import private_terms, schema

DRAFT = (
    "Meet Zorblat Notes, the notebook that files itself. "
    "Every page you write is sorted into a project before you close the lid. "
    "Early supporters pay nine dollars a month for life."
)
TERMS = ["Project Falcon", "Zorblat"]


def _guard(terms=TERMS, draft=DRAFT):
    return private_terms.parse(json.dumps({"terms": terms, "draft": draft}))


# --- matching ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [
    "projectfalcon",
    "project-falcon",
    "project_falcon",
    "Project Falcon",
    "PROJECT.FALCON",
    "r/ProjectFalconApp",
    "#projectfalcon launch",
    "https://project-falcon.example.com/pricing",
])
def test_a_two_word_name_is_caught_however_it_is_joined(value):
    assert private_terms.contains(_guard(), value)


@pytest.mark.parametrize("value", [
    "note taking apps",
    "falcon",
    "project management for falconers",
    "",
])
def test_category_words_and_partial_names_pass(value):
    assert not private_terms.contains(_guard(), value)


def test_a_quoted_sentence_of_the_draft_is_caught_and_the_category_is_not():
    guard = _guard()
    assert private_terms.contains(guard, "reactions to: every page you write is sorted into a project")
    assert private_terms.contains(guard, "Sorted into a project, before you CLOSE the lid!")
    assert not private_terms.contains(guard, "self organizing notebook app launch")
    # Four shared words in a row are below the run length.
    assert not private_terms.contains(guard, "page you write is")


def test_a_draft_shorter_than_the_run_length_matches_as_a_whole():
    guard = _guard(terms=[], draft="Ship faster today")
    assert private_terms.contains(guard, "tagline: ship faster today")
    assert not private_terms.contains(guard, "ship faster")


def test_nested_host_input_is_searched_in_keys_values_and_dataclasses():
    guard = _guard()
    plan = {"subqueries": [{"label": "primary", "search_query": "project falcon reviews"}]}
    assert private_terms.contains(guard, plan)
    assert private_terms.contains(guard, {"Project-Falcon": {"x_handle": "someone"}})
    item = schema.SourceItem(item_id="1", source="x", title="t", body="hello zorblat", url="")
    assert private_terms.contains(guard, [item])
    assert not private_terms.contains(guard, {"subqueries": [{"search_query": "notebook apps"}], "n": 3})


# --- the file ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "",
    "not json",
    "[]",
    "{}",
    '{"terms": [], "draft": "  "}',
    '{"terms": "Project Falcon"}',
    '{"terms": ["ok name", 7]}',
    '{"terms": ["a."]}',
    '{"draft": 12}',
])
def test_an_empty_or_malformed_file_is_rejected(text):
    with pytest.raises(private_terms.PrivateFileError):
        private_terms.parse(text)


def test_errors_and_the_guard_never_show_the_contents(tmp_path):
    with pytest.raises(private_terms.PrivateFileError) as excinfo:
        private_terms.parse('{"terms": ["Project Falcon", "z"], "draft": "Zorblat Notes ships"}')
    assert "falcon" not in str(excinfo.value).lower() and "zorblat" not in str(excinfo.value).lower()
    shown = repr(_guard()).lower() + str(_guard()).lower()
    assert "falcon" not in shown and "zorblat" not in shown and "notebook" not in shown


def test_load_reads_a_file_and_refuses_a_missing_one(tmp_path):
    path = tmp_path / "private.json"
    path.write_text(json.dumps({"terms": TERMS, "draft": DRAFT}), encoding="utf-8")
    assert private_terms.contains(private_terms.load(str(path)), "zorblat")
    with pytest.raises(private_terms.PrivateFileError):
        private_terms.load(str(tmp_path / "absent.json"))


# --- the engine refuses before sending --------------------------------------------------

SECRETS = ("falcon", "zorblat", "files itself", "sorted into", "nine dollars")


@pytest.fixture
def private_file(tmp_path):
    path = tmp_path / "private.json"
    path.write_text(json.dumps({"terms": TERMS, "draft": DRAFT}), encoding="utf-8")
    return str(path)


@pytest.fixture
def config_dir(monkeypatch, tmp_path):
    path = tmp_path / "config"
    monkeypatch.setattr(cli.env, "CONFIG_DIR", path)
    return path


def _refused(monkeypatch, capsys, argv, stdin=None):
    """Run the CLI with every outbound step rigged to fail the test."""
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(sys, "argv", ["last30days.py", *argv])
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    for name in ("run", "diagnose"):
        monkeypatch.setattr(cli.pipeline, name, mock.Mock(side_effect=AssertionError(f"pipeline.{name} ran")))
    try:
        rc = cli.main()
    except SystemExit as exc:
        rc = exc.code
    out = capsys.readouterr()
    shown = (out.out + out.err).lower()
    assert not any(secret in shown for secret in SECRETS), shown
    return rc, out.out, out.err


def _plan(search_query):
    return json.dumps({
        "intent": "product", "freshness_mode": "balanced_recent", "cluster_mode": "none",
        "subqueries": [{
            "label": "primary", "search_query": search_query, "ranking_query": "notebook app launches",
            "sources": ["reddit"], "weight": 1.0,
        }],
    })


def test_a_plan_subquery_with_a_private_term_exits_before_any_request(monkeypatch, capsys, private_file, config_dir):
    rc, out, err = _refused(monkeypatch, capsys, [
        "notebook app launches", "--private-file", private_file, "--plan", "-",
    ], stdin=_plan("project falcon notebook reviews"))
    assert rc == 2 and not out
    assert "the --plan input" in err and "--private-file" in err
    assert not config_dir.exists()


def test_a_plan_file_quoting_the_draft_is_refused(monkeypatch, capsys, tmp_path, private_file):
    plan = tmp_path / "plan.json"
    plan.write_text(_plan("the notebook that files itself reviews"), encoding="utf-8")
    rc, _out, err = _refused(monkeypatch, capsys, [
        "notebook app launches", "--private-file", private_file, "--plan", str(plan),
    ])
    assert rc == 2 and "the --plan input" in err


@pytest.mark.parametrize("flag_args, named", [
    (["Zorblat Notes launch"], "the topic"),
    (["notebook apps", "--subreddits", "notebooks,ProjectFalcon"], "--subreddits"),
    (["notebook apps", "--dedicated-subreddits", "project_falcon"], "--dedicated-subreddits"),
    (["notebook apps", "--official-site", "project-falcon.example.com"], "--official-site"),
    (["notebook apps", "--polymarket-keywords", "notebook,zorblat"], "--polymarket-keywords"),
    (["notebook apps", "--x-handle", "projectfalcon"], "--x-handle"),
    (["notebook apps", "--tiktok-hashtags", "#projectfalcon"], "--tiktok-hashtags"),
    (["Notely vs Pagewise", "--competitors-plan",
      '{"Pagewise": {"x_handle": "pagewise", "subreddits": ["zorblat"]}}'], "--competitors-plan"),
])
def test_a_private_term_in_any_host_argument_is_refused(monkeypatch, capsys, private_file, flag_args, named):
    rc, out, err = _refused(monkeypatch, capsys, [*flag_args, "--private-file", private_file])
    assert rc == 2 and not out and named in err


def test_a_comparison_plan_file_entry_with_a_private_term_is_refused(monkeypatch, capsys, tmp_path, private_file):
    plan = tmp_path / "peers.json"
    plan.write_text(json.dumps({"Pagewise": {"context": "rival of Project Falcon"}}), encoding="utf-8")
    rc, _out, err = _refused(monkeypatch, capsys, [
        "Notely vs Pagewise", "--competitors-plan", str(plan), "--private-file", private_file,
    ])
    assert rc == 2 and "the --competitors-plan input" in err


def test_a_host_written_open_questions_file_is_checked(monkeypatch, capsys, tmp_path, private_file):
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps({
        "schema": "last30days-open-questions/1",
        "items": [{"kind": "release", "question": "Zorblat Notes 1.0 ships", "date": "2026-12-01",
                   "url": "https://example.com/roadmap"}],
    }), encoding="utf-8")
    rc, _out, err = _refused(monkeypatch, capsys, [
        "notebook apps", "--open-questions", str(questions), "--private-file", private_file,
    ])
    assert rc == 2 and "the --open-questions file" in err


def test_a_category_only_plan_passes_the_check(monkeypatch, capsys, private_file):
    with pytest.raises(AssertionError, match="pipeline.diagnose ran"):
        _refused(monkeypatch, capsys, [
            "notebook app launches", "--private-file", private_file, "--plan", "-",
        ], stdin=_plan("self organizing notebook app launch"))


def test_a_resolved_handle_with_a_private_term_stops_the_run(monkeypatch, capsys, private_file):
    from lib import resolve

    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "notebook app launches", "--auto-resolve", "--private-file", private_file,
    ])
    monkeypatch.setattr(cli.pipeline, "diagnose", mock.Mock(return_value={"available_sources": ["reddit"]}))
    monkeypatch.setattr(cli.pipeline, "run", mock.Mock(side_effect=AssertionError("research ran")))
    monkeypatch.setattr(resolve, "auto_resolve", mock.Mock(return_value={
        "subreddits": ["notebooks"], "github_user": "ProjectFalconHQ",
    }))
    monkeypatch.setattr(cli, "_law7_host_plan_gate", lambda args, topic: None)
    rc = cli.main()
    err = capsys.readouterr().err
    assert rc == 2 and "Refused: a resolved handle" in err


def test_the_check_runs_before_the_reasoning_provider_when_a_key_is_configured(monkeypatch, capsys, private_file):
    from lib import providers, resolve

    monkeypatch.setenv("OPENAI_API_KEY", "dummy-test-key-not-real")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-test-key-not-real")
    called = mock.Mock(side_effect=AssertionError("reasoning provider called"))
    monkeypatch.setattr(providers, "resolve_runtime", called)
    monkeypatch.setattr(resolve, "auto_resolve", called)
    rc, _out, err = _refused(monkeypatch, capsys, [
        "notebook app launches", "--private-file", private_file, "--plan", "-",
    ], stdin=_plan("zorblat notes first impressions"))
    assert rc == 2 and "the --plan input" in err
    called.assert_not_called()


@pytest.mark.parametrize("contents", [None, "", "{}", "not json"])
def test_an_empty_or_unreadable_private_file_exits_without_running(monkeypatch, capsys, tmp_path, contents):
    path = tmp_path / "private.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    rc, out, err = _refused(monkeypatch, capsys, ["notebook apps", "--private-file", str(path)])
    assert rc == 2 and not out and "--private-file" in err and "nothing was sent" in err


@pytest.mark.parametrize("argv", [
    ["--discover", "ai"],
    ["--drill"],
    ["doctor"],
    ["forecast", "list"],
    ["library", "search", "notebooks"],
    ["notebook apps", "--preflight"],
    ["notebook apps", "--diagnose"],
    ["notebook apps", "--output", "brief.md"],
    ["notebook apps", "--store"],
])
def test_the_private_file_is_never_accepted_and_ignored(monkeypatch, capsys, private_file, argv):
    rc, out, err = _refused(monkeypatch, capsys, [*argv, "--private-file", private_file])
    assert rc == 2 and not out and "--private-file" in err


# --- forecast add -----------------------------------------------------------------------


def _rehearsal_payload(claim):
    return json.dumps({"topic": "codename-heron", "kind": "rehearsal", "predictions": [{
        "claim": claim, "probability": 0.7, "resolve_by": "2026-12-20",
        "resolution": "The highest-voted comment on the launch thread is about price",
        "basis": ["https://example.com/thread/1"],
    }]})


def test_a_forecast_claim_with_a_private_term_is_not_logged(monkeypatch, capsys, tmp_path, private_file):
    rc, out, err = _refused(monkeypatch, capsys, [
        "forecast", "add", "--forecast-file", "-", "--private-file", private_file,
        "--save-dir", str(tmp_path / "memory"),
    ], stdin=_rehearsal_payload("The top comment on the Zorblat Notes thread raises pricing"))
    assert rc == 2 and not out and "forecast add:" in err and "nothing was written" in err
    assert not (tmp_path / "memory" / "research.db").exists()


def test_a_clean_forecast_payload_passes_and_a_private_codename_does_not(monkeypatch, private_file):
    monkeypatch.setattr(cli, "_private_guard", private_terms.load(private_file))
    clean = json.loads(_rehearsal_payload("The top comment on the launch thread raises pricing"))
    assert cli._forecast_private_refusal(clean) is None
    clean["topic"] = "project-falcon"
    assert cli._forecast_private_refusal(clean)


# --- a guarded run leaves nothing behind ------------------------------------------------


def test_a_run_given_the_private_file_leaves_no_cache_no_saved_file_and_no_trace(
    monkeypatch, capsys, tmp_path, private_file, config_dir,
):
    memory = tmp_path / "memory"
    fixtures = tmp_path / "fixtures"
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setenv("LAST30DAYS_MEMORY_DIR", str(memory))
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "notebook app launches", "--mock", "--private-file", private_file,
        "--record-fixtures", str(fixtures),
    ])
    assert cli.main() == 0
    out = capsys.readouterr()
    assert "# last30days" in out.out and "not saving a brief" in out.err
    assert not (config_dir / "last-report.json").exists()
    assert not (config_dir / "open-questions").exists()
    assert not memory.exists()
    written = "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for root in (config_dir, fixtures) if root.exists()
        for path in root.rglob("*") if path.is_file()
    )
    shown = (out.out + out.err + written).lower()
    assert not any(secret in shown for secret in SECRETS)


def test_the_same_run_without_the_private_file_does_cache_and_save(monkeypatch, capsys, tmp_path, config_dir):
    memory = tmp_path / "memory"
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setenv("LAST30DAYS_MEMORY_DIR", str(memory))
    monkeypatch.setattr(sys, "argv", ["last30days.py", "notebook app launches", "--mock"])
    assert cli.main() == 0
    capsys.readouterr()
    assert (config_dir / "last-report.json").exists() and any(memory.iterdir())
