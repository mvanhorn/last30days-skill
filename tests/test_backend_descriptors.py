"""U2: backend-chain descriptors with predicted selection (lib/backends.py).

Chained sources declare their routing once — imported from the definitions
lib/env.py already owns — and ``backends.resolve`` produces a truthful
"will use" prediction for alternative chains (X, YouTube, web search) plus
honest conditional wording for Reddit.

Covers the plan's U2 scenarios:
  1. X with cookies present, bird healthy, no XAI key -> predicted ``bird``.
  2. Pin var set to a later backend -> pin honored and marked pinned.
  3. Preferred backend installed-but-unauthenticated does not shadow a
     fully-usable fallback (collect-then-pick).
  4. No backend usable -> tier error; prescription from the highest-priority
     backend.
  5. Paid lanes probe key presence ONLY — no network, no subprocess.
  6. Reddit renders conditional wording (default + backfill), never a
     computed winner; a scrapecreators pin renders as pinned.
  7. Parity: descriptor prediction == pipeline's pre-failover X selection
     (env.x_backend_chain()[0]) across three config permutations.
"""

from unittest import mock

import pytest

from lib import backends, env, health, xurl_x


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _probe_dep(status_map=None, default_status=health.OK):
    """Build a fake health.probe_dependency honoring a per-name status map."""
    status_map = status_map or {}

    def fake(name, timeout=health.PROBE_TIMEOUT):
        status = status_map.get(name, default_status)
        if status == health.OK:
            return health.DependencyProbe(name=name, status=health.OK, detail=f"{name} 1.0.0")
        return health.DependencyProbe(
            name=name,
            status=status,
            detail=f"{name} probe simulated {status}",
            prescription=f"reinstall {name}" if status != health.MISSING else f"install {name}",
            owner_pkg_manager="brew",
        )

    return fake


def _x_env(
    bird_installed=False,
    xurl_installed=False,
    xurl_authed=False,
    node_status=health.OK,
):
    """Context managers configuring the X-chain probe environment.

    ``xurl_authed`` drives BOTH auth surfaces consistently: the research-time
    network check (``is_available``) and the doctor-path local evidence
    (``stored_auth_status``/``has_stored_auth``) — a real machine where the
    user logged in has both.
    """
    stored = (
        (xurl_x.AUTH_OK, "stored OAuth credentials found in ~/.xurl")
        if xurl_authed
        else (xurl_x.AUTH_MISSING, "no token store at ~/.xurl")
    )
    return (
        mock.patch("lib.bird_x.is_bird_installed", return_value=bird_installed),
        mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None),
        mock.patch("lib.xurl_x.is_available", return_value=xurl_authed),
        mock.patch(
            "lib.backends.which",
            lambda name: "/usr/local/bin/xurl" if (name == "xurl" and xurl_installed) else None,
        ),
        mock.patch("lib.health.probe_dependency", _probe_dep({"node": node_status})),
        mock.patch("lib.xurl_x.stored_auth_status", return_value=stored),
        mock.patch(
            "lib.xurl_x.has_stored_auth",
            return_value=xurl_installed and xurl_authed,
        ),
    )


def _resolve_x(config, **envkw):
    with _stack(_x_env(**envkw)):
        return backends.resolve("x", config)


class _stack:
    """Enter/exit a tuple of context managers (contextlib.ExitStack, terse)."""

    def __init__(self, ctxs):
        self._ctxs = ctxs

    def __enter__(self):
        for c in self._ctxs:
            c.__enter__()
        return self

    def __exit__(self, *exc):
        for c in reversed(self._ctxs):
            c.__exit__(*exc)
        return False


# ---------------------------------------------------------------------------
# Descriptor registry: routing declared once, imported from env.py (KTD 6)
# ---------------------------------------------------------------------------

class TestDescriptorRegistry:
    def test_x_chain_comes_from_env_definitions(self):
        d = backends.get_descriptor("x")
        assert d.mode == backends.MODE_ALTERNATIVE
        assert tuple(s.name for s in d.backends) == env.X_BACKEND_ORDER
        assert env.X_BACKEND_ORDER == ("xai", "bird", "xurl", "xquik")
        assert d.pin_var == env.X_BACKEND_PIN_VAR == "LAST30DAYS_X_BACKEND"

    def test_env_exposes_reddit_pin_constants(self):
        assert env.REDDIT_BACKEND_PIN_VAR == "LAST30DAYS_REDDIT_BACKEND"
        assert env.REDDIT_SC_MIN_ITEMS_VAR == "LAST30DAYS_REDDIT_SC_MIN_ITEMS"

    def test_youtube_and_web_chains_declared_in_order(self):
        yt = backends.get_descriptor("youtube")
        assert tuple(s.name for s in yt.backends) == ("yt-dlp", "scrapecreators")
        web = backends.get_descriptor("web")
        assert tuple(s.name for s in web.backends) == (
            "brave", "exa", "serper", "parallel", "keyless",
        )
        assert web.pin_flag == "--web-backend"

    def test_reddit_is_conditional_and_lanes_are_not_chain_entries(self):
        d = backends.get_descriptor("reddit")
        assert d.mode == backends.MODE_CONDITIONAL
        names = [s.name for s in d.backends]
        # Internal keyless lanes are sub-probe detail, never chain entries.
        for lane in ("rss", "listing", "arctic", "shreddit"):
            assert lane not in names
        assert names == ["public", "scrapecreators"]

    def test_unknown_source_raises(self):
        with pytest.raises(KeyError):
            backends.get_descriptor("nope")
        with pytest.raises(KeyError):
            backends.resolve("nope", {})


# ---------------------------------------------------------------------------
# Scenario 1: cookies present, bird healthy, no XAI key -> bird predicted
# ---------------------------------------------------------------------------

class TestXPrediction:
    def test_bird_predicted_with_cookies_and_no_xai_key(self):
        config = {"AUTH_TOKEN": "dummy-token", "CT0": "dummy-ct0"}
        res = _resolve_x(config, bird_installed=True)
        assert res.active_backend == "bird"
        assert res.tier == backends.TIER_OK
        assert res.pinned is False
        # Chain rendered in declared order regardless of availability.
        assert res.chain == list(env.X_BACKEND_ORDER)
        assert [f.name for f in res.findings] == list(env.X_BACKEND_ORDER)
        assert "will use: bird" in res.summary

    # Scenario 2: pin var set to a later backend -> honored + marked pinned.
    def test_pin_to_later_backend_honored_and_marked(self):
        config = {
            "AUTH_TOKEN": "dummy-token",
            "CT0": "dummy-ct0",
            "XQUIK_API_KEY": "dummy-key",
            "LAST30DAYS_X_BACKEND": "xquik",
        }
        res = _resolve_x(config, bird_installed=True)
        assert res.active_backend == "xquik"
        assert res.pinned is True
        assert res.pin == "xquik"
        assert res.tier == backends.TIER_OK
        assert "pinned" in res.summary

    # Scenario 3: installed-but-unauthenticated preferred backend must not
    # shadow a fully usable fallback (collect-then-pick).
    def test_unauthenticated_preferred_does_not_shadow_usable_fallback(self):
        config = {"XQUIK_API_KEY": "dummy-key"}
        res = _resolve_x(config, xurl_installed=True, xurl_authed=False)
        assert res.active_backend == "xquik"
        assert res.tier == backends.TIER_OK
        xurl = next(f for f in res.findings if f.name == "xurl")
        assert not xurl.usable
        assert "auth" in (xurl.detail + xurl.prescription).lower()

    # Scenario 4: nothing usable -> error tier, highest-priority prescription.
    def test_no_backend_usable_is_error_with_top_priority_prescription(self):
        res = _resolve_x({})
        assert res.active_backend is None
        assert res.tier == backends.TIER_ERROR
        assert "XAI_API_KEY" in res.prescription

    def test_pinned_but_unusable_backend_is_error_with_its_prescription(self):
        # Pin bird without cookies: env.x_backend_chain returns [] (pipeline
        # raises); resolution mirrors that as an error carrying bird's fix.
        config = {"LAST30DAYS_X_BACKEND": "bird"}
        res = _resolve_x(config, bird_installed=True)
        assert res.active_backend is None
        assert res.pinned is True
        assert res.tier == backends.TIER_ERROR
        assert res.prescription  # bird's cookie prescription, not xai's
        assert "XAI_API_KEY" not in res.prescription

    def test_broken_node_shim_makes_bird_unusable_and_falls_back(self):
        # U1 integration: a stale node shim (BROKEN, not missing) must not
        # let bird read as usable — the #692 class applied to chains.
        config = {
            "AUTH_TOKEN": "dummy-token",
            "CT0": "dummy-ct0",
            "XQUIK_API_KEY": "dummy-key",
        }
        res = _resolve_x(config, bird_installed=True, node_status=health.BROKEN)
        assert res.active_backend == "xquik"
        bird = next(f for f in res.findings if f.name == "bird")
        assert bird.status == health.BROKEN
        assert not bird.usable

    def test_unconfigured_x_with_broken_node_is_unconfigured_not_node_error(self):
        # F9: cookie presence is checked BEFORE the node runtime. With no X
        # configuration at all, a broken node must not turn bird into a
        # BROKEN finding carrying a node prescription — the honest state is
        # "unconfigured, here is the cookie fix" (which doctor rolls up to
        # tier off, since every finding is MISSING).
        res = _resolve_x({}, node_status=health.BROKEN)
        bird = next(f for f in res.findings if f.name == "bird")
        assert bird.status == health.MISSING
        assert "AUTH_TOKEN/CT0" in bird.detail
        assert "cookie" in bird.prescription.lower()
        assert "node" not in bird.prescription.lower()
        # Doctor's off/unconfigured rollup keys on all-findings-MISSING.
        assert all(f.status == health.MISSING for f in res.findings)

    def test_cookies_present_broken_node_still_reads_broken(self):
        # The inverse guard: once cookies ARE configured, a broken node is a
        # real configured-but-broken state and must keep the node fix.
        config = {"AUTH_TOKEN": "dummy-token", "CT0": "dummy-ct0"}
        res = _resolve_x(config, bird_installed=True, node_status=health.BROKEN)
        bird = next(f for f in res.findings if f.name == "bird")
        assert bird.status == health.BROKEN
        assert "node" in bird.prescription.lower()


# ---------------------------------------------------------------------------
# Scenario 5: paid lanes probe key presence only — never network/subprocess
# ---------------------------------------------------------------------------

def _forbid_io():
    def boom(*a, **k):
        raise AssertionError("paid-lane probe attempted I/O")

    return (
        mock.patch("socket.socket", boom),
        mock.patch("socket.create_connection", boom),
        mock.patch("urllib.request.urlopen", boom),
        mock.patch("subprocess.run", boom),
        mock.patch("subprocess.Popen", boom),
    )


class TestPaidLaneProbes:
    PAID = [
        ("x", "xai", "XAI_API_KEY"),
        ("x", "xquik", "XQUIK_API_KEY"),
        ("web", "serper", "SERPER_API_KEY"),
        ("youtube", "scrapecreators", "SCRAPECREATORS_API_KEY"),
        ("reddit", "scrapecreators", "SCRAPECREATORS_API_KEY"),
    ]

    def test_paid_lanes_are_flagged_paid(self):
        for source, name, _key in self.PAID:
            spec = next(
                s for s in backends.get_descriptor(source).backends if s.name == name
            )
            assert spec.paid is True, f"{source}/{name} must be a paid (key-only) lane"

    def test_key_presence_probe_makes_no_network_or_subprocess_calls(self):
        ctxs = _forbid_io()
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
            for source, name, key in self.PAID:
                spec = next(
                    s for s in backends.get_descriptor(source).backends if s.name == name
                )
                present = spec.probe({key: "dummy-key"})
                assert present.status == health.OK
                absent = spec.probe({})
                assert absent.status == health.MISSING
                assert key in absent.prescription


# ---------------------------------------------------------------------------
# F1 + F10: the doctor-path xurl probe is LOCAL-ONLY (stored-token evidence,
# never a live `xurl whoami` — doctor's no-network guarantee) and typed.
# ---------------------------------------------------------------------------

class TestXurlLocalProbe:
    def _spec(self):
        return next(
            s for s in backends.get_descriptor("x").backends if s.name == "xurl"
        )

    def _finding(self, stored, installed=True):
        """Run the xurl probe under the forbid-all-I/O harness."""
        ctxs = _forbid_io()
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], \
             mock.patch(
                 "lib.backends.which",
                 lambda n: "/usr/local/bin/xurl" if installed else None,
             ), \
             mock.patch("lib.xurl_x.stored_auth_status", return_value=stored):
            return self._spec().probe({})

    def test_token_store_present_is_ok_without_network(self):
        finding = self._finding(
            (xurl_x.AUTH_OK, "stored OAuth credentials found in ~/.xurl")
        )
        assert finding.status == health.OK
        assert "not live-verified" in finding.detail

    def test_no_token_store_is_missing_with_auth_prescription(self):
        finding = self._finding((xurl_x.AUTH_MISSING, "no token store at ~/.xurl"))
        assert finding.status == health.MISSING
        assert "not authenticated" in finding.detail
        assert "xurl auth oauth2 login" in finding.prescription

    def test_unreadable_token_store_is_error_tier(self):
        # F10: binary resolvable but the token-store read fails -> typed
        # ERROR (doctor's error tier), never "unconfigured".
        finding = self._finding(
            (
                xurl_x.AUTH_ERROR,
                "token store ~/.xurl unreadable: PermissionError: denied",
            )
        )
        assert finding.status == health.ERROR
        assert not finding.usable
        assert "unreadable" in finding.detail

    def test_binary_absent_stays_not_installed(self):
        finding = self._finding((xurl_x.AUTH_OK, "irrelevant"), installed=False)
        assert finding.status == health.MISSING
        assert "not found on PATH" in finding.detail

    def test_whole_doctor_path_x_probe_makes_no_network_or_subprocess(self, tmp_path):
        """The full X chain resolution plus the safe get_x_source_status —
        the exact X probes doctor runs — under the forbid-everything
        harness. The token store is a REAL file so the genuine
        stored_auth_status code path (filesystem only) is exercised."""
        store = tmp_path / ".xurl"
        store.write_text(
            "apps:\n  app:\n    oauth2_tokens:\n      me:\n        oauth2:\n"
            "          access_token: dummy-not-real\n",
            encoding="utf-8",
        )
        config = {"AUTH_TOKEN": "dummy-token", "CT0": "dummy-ct0"}
        bird_status = {
            "installed": True,
            "authenticated": True,
            "username": "env AUTH_TOKEN",
            "can_install": True,
        }
        ctxs = _forbid_io()
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], \
             mock.patch(
                 "lib.xurl_x.is_available",
                 side_effect=AssertionError(
                     "doctor path ran the live `xurl whoami` network check"
                 ),
             ), \
             mock.patch("lib.xurl_x.token_store_path", return_value=store), \
             mock.patch("lib.backends.which", lambda n: f"/usr/local/bin/{n}"), \
             mock.patch(
                 "lib.xurl_x.shutil.which", lambda n: f"/usr/local/bin/{n}"
             ), \
             mock.patch("lib.health.probe_dependency", _probe_dep()), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True), \
             mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None), \
             mock.patch("lib.bird_x.get_bird_status", return_value=bird_status):
            res = backends.resolve("x", config)
            status = env.get_x_source_status(config, probe=False)
        xurl_finding = next(f for f in res.findings if f.name == "xurl")
        assert xurl_finding.status == health.OK
        assert "not live-verified" in xurl_finding.detail
        assert status["xurl_available"] is True


# ---------------------------------------------------------------------------
# Scenario 6: Reddit conditional wording, never a computed winner
# ---------------------------------------------------------------------------

class TestRedditConditional:
    def test_sc_key_present_renders_default_plus_backfill_no_winner(self):
        res = backends.resolve("reddit", {"SCRAPECREATORS_API_KEY": "dummy-key"})
        assert res.mode == backends.MODE_CONDITIONAL
        assert res.active_backend is None  # never a single computed winner
        assert "will use" not in res.summary
        low = res.conditional.lower()
        assert "public keyless" in low
        assert "default" in low
        assert "scrapecreators backfill" in low
        assert res.tier == backends.TIER_OK

    def test_thinness_floor_appears_in_wording(self):
        res = backends.resolve(
            "reddit",
            {"SCRAPECREATORS_API_KEY": "dummy-key", "LAST30DAYS_REDDIT_SC_MIN_ITEMS": "5"},
        )
        assert "5" in res.conditional
        assert "floor" in res.conditional.lower()

    def test_default_floor_zero_means_empty_only_wording(self):
        res = backends.resolve("reddit", {"SCRAPECREATORS_API_KEY": "dummy-key"})
        assert "nothing" in res.conditional.lower()

    def test_malformed_floor_treated_as_default(self):
        res = backends.resolve(
            "reddit",
            {"SCRAPECREATORS_API_KEY": "dummy-key", "LAST30DAYS_REDDIT_SC_MIN_ITEMS": "lots"},
        )
        assert "nothing" in res.conditional.lower()

    def test_pinned_scrapecreators_renders_pin(self):
        res = backends.resolve(
            "reddit",
            {
                "SCRAPECREATORS_API_KEY": "dummy-key",
                "LAST30DAYS_REDDIT_BACKEND": "scrapecreators",
            },
        )
        assert res.pinned is True
        assert res.pin == "scrapecreators"
        low = res.conditional.lower()
        assert "pinned" in low
        assert "primary" in low
        assert res.active_backend is None  # still conditional, not a winner

    def test_no_key_means_no_backfill_wording(self):
        res = backends.resolve("reddit", {})
        low = res.conditional.lower()
        assert "public keyless" in low
        assert "backfill" not in low or "no scrapecreators" in low
        assert res.tier == backends.TIER_OK  # public composite always reachable

    def test_pin_without_key_is_ignored_like_the_pipeline(self):
        # pipeline gates sc_first on has_sc_key; the pin alone changes nothing.
        res = backends.resolve(
            "reddit", {"LAST30DAYS_REDDIT_BACKEND": "scrapecreators"},
        )
        assert res.pinned is False
        assert "primary" not in res.conditional.lower().split("pin ignored")[0]

    def test_keyless_lanes_are_sub_probe_detail(self):
        res = backends.resolve("reddit", {})
        public = next(f for f in res.findings if f.name == "public")
        for lane in ("rss", "listing", "arctic", "shreddit"):
            assert lane in public.detail


# ---------------------------------------------------------------------------
# Scenario 7: parity with the pipeline's pre-failover X selection
# ---------------------------------------------------------------------------

class TestXParityWithPipeline:
    """Descriptor prediction must equal env.x_backend_chain(config)[0] — the
    exact expression pipeline._retrieve_stream uses as its pre-failover
    primary (lib/pipeline.py, `chain = env.x_backend_chain(config)`)."""

    def _assert_parity(self, config, **envkw):
        with _stack(_x_env(**envkw)):
            chain = env.x_backend_chain(config)
            predicted = backends.resolve("x", config).active_backend
        expected = chain[0] if chain else None
        assert predicted == expected, (
            f"prediction {predicted!r} != pipeline pre-failover {expected!r} "
            f"for config keys {sorted(config)}"
        )

    def test_parity_xai_key_only(self):
        self._assert_parity({"XAI_API_KEY": "dummy-key"})

    def test_parity_cookies_and_bird_installed(self):
        self._assert_parity(
            {"AUTH_TOKEN": "dummy-token", "CT0": "dummy-ct0"},
            bird_installed=True,
        )

    def test_parity_pin_forces_xquik(self):
        self._assert_parity(
            {"XQUIK_API_KEY": "dummy-key", "LAST30DAYS_X_BACKEND": "xquik"},
        )

    def test_parity_nothing_configured(self):
        self._assert_parity({})


# ---------------------------------------------------------------------------
# YouTube chain: yt-dlp -> ScrapeCreators
# ---------------------------------------------------------------------------

class TestYouTubeChain:
    def test_ytdlp_healthy_wins(self):
        with mock.patch("lib.health.probe_dependency", _probe_dep()):
            res = backends.resolve("youtube", {"SCRAPECREATORS_API_KEY": "dummy-key"})
        assert res.active_backend == "yt-dlp"
        assert res.tier == backends.TIER_OK

    def test_missing_ytdlp_falls_back_to_sc_key(self):
        with mock.patch(
            "lib.health.probe_dependency", _probe_dep({"yt-dlp": health.MISSING}),
        ):
            res = backends.resolve("youtube", {"SCRAPECREATORS_API_KEY": "dummy-key"})
        assert res.active_backend == "scrapecreators"
        assert res.tier == backends.TIER_OK

    def test_neither_available_error_carries_ytdlp_prescription(self):
        with mock.patch(
            "lib.health.probe_dependency", _probe_dep({"yt-dlp": health.MISSING}),
        ):
            res = backends.resolve("youtube", {})
        assert res.active_backend is None
        assert res.tier == backends.TIER_ERROR
        assert "yt-dlp" in res.prescription


# ---------------------------------------------------------------------------
# Web search chain: brave -> exa -> serper -> parallel -> keyless floor
# ---------------------------------------------------------------------------

class TestWebChain:
    def test_brave_key_predicted_first(self):
        res = backends.resolve(
            "web", {"BRAVE_API_KEY": "dummy-key", "EXA_API_KEY": "dummy-key"},
        )
        assert res.active_backend == "brave"
        assert res.tier == backends.TIER_OK

    def test_keyless_floor_is_degraded_warn(self):
        res = backends.resolve("web", {})
        assert res.active_backend == "keyless"
        assert res.tier == backends.TIER_WARN

    def test_native_search_suppresses_keyless_floor(self):
        res = backends.resolve("web", {"LAST30DAYS_NATIVE_SEARCH": "1"})
        keyless = next(f for f in res.findings if f.name == "keyless")
        assert not keyless.usable
        assert res.active_backend is None

    def test_pin_via_web_backend_flag(self):
        res = backends.resolve(
            "web", {"BRAVE_API_KEY": "dummy-key", "EXA_API_KEY": "dummy-key"}, pin="exa",
        )
        assert res.active_backend == "exa"
        assert res.pinned is True
        assert "pinned" in res.summary

    def test_parity_with_grounding_auto_dispatch(self):
        """resolve('web').active_backend must match the backend grounding's
        auto branch actually dispatches to, per config permutation."""
        from lib import grounding

        def _auto_pick(config):
            picked = {}

            def rec(label):
                def f(query, date_range, key, count=5):
                    picked["backend"] = label
                    return [], {"label": label}
                return f

            with mock.patch.object(grounding, "brave_search", rec("brave")), \
                 mock.patch.object(grounding, "exa_search", rec("exa")), \
                 mock.patch.object(grounding, "serper_search", rec("serper")), \
                 mock.patch.object(grounding, "parallel_search", rec("parallel")), \
                 mock.patch(
                     "lib.web_search_keyless.keyless_search",
                     lambda q, dr, cfg: (picked.__setitem__("backend", "keyless") or ([], {})),
                 ):
                grounding.web_search("q", ("2026-06-04", "2026-07-04"), config, backend="auto")
            return picked.get("backend")

        for config in (
            {"BRAVE_API_KEY": "dummy-key"},
            {"SERPER_API_KEY": "dummy-key"},
            {},
        ):
            assert backends.resolve("web", config).active_backend == _auto_pick(config)


# ---------------------------------------------------------------------------
# Rendering: prediction reads as will-use, never as past observation
# ---------------------------------------------------------------------------

class TestSummaryWording:
    def test_alternative_summary_is_will_use(self):
        res = backends.resolve("web", {"BRAVE_API_KEY": "dummy-key"})
        assert res.summary.startswith("will use: brave")
        assert "used" not in res.summary.split("will use")[1]

    def test_error_summary_names_no_backend(self):
        with mock.patch(
            "lib.health.probe_dependency", _probe_dep({"yt-dlp": health.MISSING}),
        ):
            res = backends.resolve("youtube", {})
        assert "will use" not in res.summary
        assert "no usable backend" in res.summary.lower()

    def test_conditional_summary_is_the_conditional_wording(self):
        res = backends.resolve("reddit", {"SCRAPECREATORS_API_KEY": "dummy-key"})
        assert res.summary == res.conditional
