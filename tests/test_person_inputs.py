"""Person run inputs: --employer, --linkedin, and --email.

A person run starts only when the host passes one of these flags for a person
topic. A name-only run behaves exactly as before. The flags build the identity
anchors later stages filter on: the employer name and domain, and the email
domain unless it is a personal-mail provider, which identifies no one.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest

import last30days as cli
from lib import person, schema

NAME = "Dana Okafor"


def _cli(argv, tmp_path, *, run=None):
    """Run ``main`` with research and config probes stubbed out."""
    out, err = io.StringIO(), io.StringIO()
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(exist_ok=True)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(cli.env, "get_config", return_value={}))
        stack.enter_context(mock.patch.object(cli.env, "CONFIG_DIR", cfg_dir))
        stack.enter_context(mock.patch.object(cli.env, "CONFIG_FILE", cfg_dir / ".env"))
        stack.enter_context(mock.patch.object(cli.ui, "ProgressDisplay", return_value=mock.Mock()))
        stack.enter_context(mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1"}))
        stack.enter_context(mock.patch.object(sys, "argv", ["last30days.py", *argv]))
        stack.enter_context(mock.patch.object(
            cli.pipeline, "run", side_effect=run or AssertionError("pipeline.run must not run"),
        ))
        stack.enter_context(redirect_stdout(out))
        stack.enter_context(redirect_stderr(err))
        rc = cli.main()
    return rc, out.getvalue(), err.getvalue()


def _capturing_run(store: dict):
    def fake_run(**kwargs):
        store.update(kwargs)
        topic = kwargs["topic"]
        return schema.Report(
            topic=topic, range_from="2026-10-20", range_to="2026-11-19",
            generated_at="2026-11-19T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="local", planner_model="d", rerank_model="l",
            ),
            query_plan=schema.QueryPlan(
                intent="general", freshness_mode="balanced_recent", cluster_mode="story",
                raw_topic=topic, subqueries=[], source_weights={},
            ),
            clusters=[], ranked_candidates=[], items_by_source={}, errors_by_source={},
        )
    return fake_run


class TestParser:
    def test_person_flags_parse_as_strings(self):
        args = cli.build_parser().parse_args([
            NAME,
            "--employer", "Northwind Freight:northwind.example",
            "--linkedin", "https://www.linkedin.com/in/dana-okafor",
            "--email", "dana@northwind.example",
        ])
        assert args.employer == "Northwind Freight:northwind.example"
        assert args.linkedin == "https://www.linkedin.com/in/dana-okafor"
        assert args.email == "dana@northwind.example"

    def test_name_only_run_has_no_person_inputs(self):
        args = cli.build_parser().parse_args([NAME])
        assert (args.employer, args.linkedin, args.email) == (None, None, None)
        assert person.build_person_context(
            NAME, employer=args.employer, linkedin=args.linkedin, email=args.email,
        ) is None


class TestAnchors:
    def test_employer_name_and_domain_are_anchors(self):
        ctx = person.build_person_context(NAME, employer="Northwind Freight:northwind.example")
        assert ctx.full_name == NAME
        assert ctx.employer_name == "Northwind Freight"
        assert ctx.employer_domain == "northwind.example"
        assert ctx.anchors == ["Northwind Freight", "northwind.example"]
        assert ctx.identity_weak is False

    def test_employer_site_url_reduces_to_its_domain(self):
        ctx = person.build_person_context(
            NAME, employer="Northwind Freight:https://www.northwind.example/team",
        )
        assert ctx.employer_domain == "northwind.example"
        assert ctx.anchors == ["Northwind Freight", "northwind.example"]

    def test_employer_without_domain_anchors_the_name(self):
        ctx = person.build_person_context(NAME, employer="Northwind Freight")
        assert ctx.employer_domain == ""
        assert ctx.anchors == ["Northwind Freight"]

    def test_work_email_adds_its_domain(self):
        ctx = person.build_person_context(NAME, email="Dana@Northwind.example")
        assert ctx.email_domain == "northwind.example"
        assert ctx.anchors == ["northwind.example"]
        assert ctx.identity_weak is False

    def test_email_domain_matching_employer_domain_is_one_anchor(self):
        ctx = person.build_person_context(
            NAME, employer="Northwind Freight:northwind.example", email="dana@northwind.example",
        )
        assert ctx.anchors == ["Northwind Freight", "northwind.example"]

    @pytest.mark.parametrize("address", [
        "dana@gmail.com", "dana@googlemail.com", "dana@yahoo.co.uk", "dana@hotmail.fr",
        "dana@outlook.com", "dana@icloud.com", "dana@proton.me", "dana@gmx.de",
    ])
    def test_personal_email_adds_no_anchor_and_marks_identity_weak(self, address):
        ctx = person.build_person_context(NAME, email=address)
        assert ctx.anchors == []
        assert ctx.email_domain == ""
        assert ctx.identity_weak is True
        # The full address is kept for a later lookup, not discarded.
        assert ctx.email == address

    def test_personal_email_with_employer_is_not_weak(self):
        ctx = person.build_person_context(
            NAME, employer="Northwind Freight", email="dana@gmail.com",
        )
        assert ctx.anchors == ["Northwind Freight"]
        assert ctx.identity_weak is False

    def test_company_domain_named_like_a_mail_provider_is_an_anchor(self):
        ctx = person.build_person_context(NAME, email="dana@outlook-freight.example")
        assert ctx.anchors == ["outlook-freight.example"]

    def test_linkedin_url_is_normalized_and_not_weak(self):
        ctx = person.build_person_context(
            NAME, linkedin="http://uk.linkedin.com/in/Dana-Okafor/details/experience?trk=x#top",
        )
        assert ctx.linkedin_url == "https://www.linkedin.com/in/dana-okafor"
        assert ctx.identity_weak is False

    def test_status_fields_start_unset(self):
        ctx = person.build_person_context(NAME, employer="Northwind Freight")
        assert ctx.linkedin_status is None
        assert ctx.match_basis == []
        assert ctx.linkedin_mismatch == ""
        assert ctx.profile == {}
        assert ctx.bio == {}
        assert ctx.derived_handles == {}
        assert ctx.older_posts == []
        assert ctx.dropped_counts == {}
        assert ctx.timed_out is False

    def test_linkedin_status_accepts_only_known_values(self):
        ctx = person.build_person_context(NAME, employer="Northwind Freight")
        for status in ("checked", "no key", "failed", "profile not found", "match rejected"):
            ctx.set_linkedin_status(status)
            assert ctx.linkedin_status == status
        with pytest.raises(ValueError):
            ctx.set_linkedin_status("quiet")

    def test_to_dict_never_carries_the_email_address(self):
        for address in ("dana@northwind.example", "dana@gmail.com"):
            ctx = person.build_person_context(NAME, email=address)
            data = ctx.to_dict()
            assert "email" not in data
            assert address not in json.dumps(data)
            assert "dana@" not in json.dumps(data)
            assert data["full_name"] == NAME
            assert "dana@" not in repr(ctx)


class TestShapeChecks:
    @pytest.mark.parametrize("url", [
        "linkedin.com/in/dana-okafor",
        "https://www.linkedin.com/company/northwind-freight",
        "https://www.linkedin.com/in/",
        "https://evil.example/in/dana-okafor",
        "https://linkedin.com.evil.example/in/dana-okafor",
        "ftp://www.linkedin.com/in/dana-okafor",
        "https://www.linkedin.com/in/dana okafor",
    ])
    def test_malformed_linkedin_url_is_rejected(self, url):
        with pytest.raises(ValueError, match="--linkedin"):
            person.build_person_context(NAME, linkedin=url)

    @pytest.mark.parametrize("address", [
        "dana", "dana@", "@northwind.example", "dana@northwind", "dana@@northwind.example",
        "dana okafor@northwind.example", "",
    ])
    def test_malformed_email_is_rejected_without_echoing_it(self, address):
        with pytest.raises(ValueError, match="--email") as exc:
            person.build_person_context(NAME, email=address)
        if address:
            assert address not in str(exc.value)

    @pytest.mark.parametrize("employer", ["", ":northwind.example", "Northwind Freight:not a domain"])
    def test_malformed_employer_is_rejected(self, employer):
        with pytest.raises(ValueError, match="--employer"):
            person.build_person_context(NAME, employer=employer)


class TestMain:
    def test_malformed_linkedin_url_exits_2(self, tmp_path):
        rc, _, err = _cli([NAME, "--linkedin", "https://www.linkedin.com/company/northwind"], tmp_path)
        assert rc == 2
        assert "--linkedin" in err

    def test_malformed_email_exits_2_without_echoing_it(self, tmp_path):
        rc, _, err = _cli([NAME, "--email", "dana-at-northwind"], tmp_path)
        assert rc == 2
        assert "--email" in err and "dana-at-northwind" not in err

    @pytest.mark.parametrize("argv", [
        ["Dana Okafor vs Lee Park", "--employer", "Northwind Freight"],
        [NAME, "--competitors-list", "Lee Park", "--email", "dana@northwind.example"],
    ])
    def test_person_flags_in_a_comparison_run_exit_2(self, tmp_path, argv):
        rc, _, err = _cli(argv, tmp_path)
        assert rc == 2
        assert "comparison" in err
        assert "dana@" not in err

    def test_person_flags_without_a_topic_exit_2(self, tmp_path):
        rc, _, err = _cli(["--employer", "Northwind Freight"], tmp_path)
        assert rc == 2
        assert "--employer" in err

    def test_valid_person_flags_reach_the_run(self, tmp_path):
        captured: dict = {}
        rc, _, err = _cli(
            [NAME, "--mock", "--employer", "Northwind Freight:northwind.example",
             "--linkedin", "https://www.linkedin.com/in/dana-okafor", "--email", "dana@gmail.com"],
            tmp_path, run=_capturing_run(captured),
        )
        assert rc == 0, err
        assert captured["topic"] == NAME
        assert "dana@gmail.com" not in err

    def test_name_only_run_config_carries_nothing_new(self, tmp_path):
        captured: dict = {}
        rc, _, err = _cli([NAME, "--mock"], tmp_path, run=_capturing_run(captured))
        assert rc == 0, err
        assert not any("person" in key.lower() for key in captured)
        assert not any("person" in str(key).lower() for key in captured["config"])
