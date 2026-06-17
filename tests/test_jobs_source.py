import unittest
from unittest.mock import patch

from lib import jobs


class JobsSourceTests(unittest.TestCase):
    def test_parse_greenhouse_response_preserves_core_fields(self):
        payload = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Enterprise Security Engineer",
                    "updated_at": "2026-06-01T10:00:00-05:00",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                    "content": "<p>Build SSO and SOC 2 workflows.</p>",
                    "location": {"name": "Remote"},
                    "departments": [{"name": "Engineering"}],
                    "offices": [{"name": "US"}],
                }
            ]
        }
        parsed = jobs.parse_greenhouse_response(payload, board_token="acme")
        self.assertEqual(1, len(parsed))
        item = parsed[0]
        self.assertEqual("Enterprise Security Engineer", item["title"])
        self.assertEqual("2026-06-01", item["date"])
        self.assertEqual("Engineering", item["department"])
        self.assertIn("SSO", item["description"])
        self.assertEqual("greenhouse", item["provider"])

    @patch("lib.jobs.http.get_text", return_value=None)
    @patch("lib.jobs.http.get")
    def test_all_ats_miss_degrades_to_empty(self, mock_get, _mock_text):
        # No careers page (get_text None), no web backend, every ATS probe 404s.
        mock_get.side_effect = jobs.http.HTTPError("missing", status_code=404)
        items, artifact = jobs.search_jobs(
            "MissingCo",
            ("2026-05-16", "2026-06-16"),
            {},
            web_backend="none",
        )
        self.assertEqual([], items)
        self.assertEqual(0, artifact["resultCount"])
        self.assertEqual("web", artifact["tier"])

    def test_detect_ats_reads_provider_and_slug_from_embed(self):
        html = '<a href="https://jobs.ashbyhq.com/listenlabs/4b17">Open roles</a>'
        provider, slug = jobs.detect_ats(html)
        self.assertEqual("ashby", provider)
        self.assertEqual("listenlabs", slug)

    def test_detect_ats_handles_greenhouse_embed_query(self):
        html = '<script src="https://boards.greenhouse.io/embed/job_board?for=acmeco"></script>'
        provider, slug = jobs.detect_ats(html)
        self.assertEqual("greenhouse", provider)
        self.assertEqual("acmeco", slug)

    def test_detect_ats_returns_none_when_no_ats(self):
        self.assertEqual((None, None), jobs.detect_ats("<html><body>About us</body></html>"))

    def test_parse_ashby_response_core_fields(self):
        payload = {
            "jobs": [
                {
                    "id": "abc",
                    "title": "Founding Research Scientist, Human Simulation",
                    "departmentName": "Research",
                    "locationName": "San Francisco",
                    "publishedDate": "2026-06-10",
                    "jobUrl": "https://jobs.ashbyhq.com/listenlabs/abc",
                    "descriptionPlain": "Build human simulation models.",
                }
            ]
        }
        parsed = jobs.parse_ashby_response(payload, slug="listenlabs")
        self.assertEqual(1, len(parsed))
        item = parsed[0]
        self.assertEqual("Founding Research Scientist, Human Simulation", item["title"])
        self.assertEqual("Research", item["department"])
        self.assertEqual("2026-06-10", item["date"])
        self.assertEqual("ashby", item["provider"])

    def test_parse_lever_response_handles_list_and_epoch(self):
        payload = [
            {
                "id": "xyz",
                "text": "Staff Engineer",
                "hostedUrl": "https://jobs.lever.co/acme/xyz",
                "categories": {"department": "Engineering", "location": "Remote"},
                "createdAt": 1749513600000,
                "descriptionPlain": "Own the platform.",
            }
        ]
        parsed = jobs.parse_lever_response(payload, slug="acme")
        self.assertEqual(1, len(parsed))
        self.assertEqual("Staff Engineer", parsed[0]["title"])
        self.assertEqual("Engineering", parsed[0]["department"])
        self.assertRegex(parsed[0]["date"], r"\d{4}-\d{2}-\d{2}")
        self.assertEqual("lever", parsed[0]["provider"])

    def test_parse_smartrecruiters_response_core_fields(self):
        payload = {
            "content": [
                {
                    "id": "777",
                    "name": "Account Executive",
                    "department": {"label": "Sales"},
                    "location": {"city": "New York", "country": "us"},
                    "releasedDate": "2026-06-02T00:00:00.000Z",
                }
            ]
        }
        parsed = jobs.parse_smartrecruiters_response(payload, slug="acme")
        self.assertEqual(1, len(parsed))
        self.assertEqual("Account Executive", parsed[0]["title"])
        self.assertEqual("Sales", parsed[0]["department"])
        self.assertEqual("2026-06-02", parsed[0]["date"])
        self.assertEqual("smartrecruiters", parsed[0]["provider"])

    def test_extract_jsonld_jobs_parses_jobposting(self):
        html = (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"JobPosting",'
            '"title":"Product Designer","datePosted":"2026-06-05",'
            '"description":"<p>Design things.</p>",'
            '"url":"https://acme.com/jobs/1",'
            '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",'
            '"addressLocality":"SF","addressCountry":"US"}}}'
            '</script>'
        )
        parsed = jobs.extract_jsonld_jobs(html, "https://acme.com/careers")
        self.assertEqual(1, len(parsed))
        self.assertEqual("Product Designer", parsed[0]["title"])
        self.assertEqual("2026-06-05", parsed[0]["date"])
        self.assertEqual("careers-jsonld", parsed[0]["provider"])
        self.assertIn("SF", parsed[0]["location"])

    def test_extract_jsonld_jobs_handles_graph_arrays(self):
        html = (
            '<script type="application/ld+json">'
            '{"@graph":[{"@type":"Organization","name":"Acme"},'
            '{"@type":"JobPosting","title":"GTM Lead","datePosted":"2026-06-01"}]}'
            '</script>'
        )
        parsed = jobs.extract_jsonld_jobs(html, "https://acme.com/careers")
        self.assertEqual(1, len(parsed))
        self.assertEqual("GTM Lead", parsed[0]["title"])

    def test_jsonld_jobs_without_urls_survive_normalize_and_dedupe(self):
        from lib import dedupe, normalize

        html = (
            '<script type="application/ld+json">'
            '{"@graph":['
            '{"@type":"JobPosting","title":"Founding Designer","datePosted":"2026-06-01"},'
            '{"@type":"JobPosting","title":"GTM Lead","datePosted":"2026-06-02"},'
            '{"@type":"JobPosting","title":"Staff Engineer","datePosted":"2026-06-03"}'
            ']}'
            '</script>'
        )
        parsed = jobs.extract_jsonld_jobs(html, "https://acme.com/careers")
        self.assertEqual(3, len(parsed))
        self.assertTrue(all(item["url"] == "" for item in parsed))
        self.assertTrue(all(item["source_url"] == "https://acme.com/careers" for item in parsed))

        normalized = normalize.normalize_source_items("jobs", parsed, "2026-05-17", "2026-06-16")
        kept = dedupe.dedupe_items(normalized)
        self.assertEqual(
            ["Founding Designer", "GTM Lead", "Staff Engineer"],
            sorted(item.title for item in kept),
        )


if __name__ == "__main__":
    unittest.main()


class JobsDateAndBannerTests(unittest.TestCase):
    def test_jobs_keep_open_roles_outside_date_window(self):
        from lib import normalize
        raw = [
            {"id": "1", "title": "Founding Research Scientist", "url": "https://x/1",
             "date": "2026-01-10", "provider": "ashby"},   # posted months ago, still open
            {"id": "2", "title": "Account Executive", "url": "https://x/2",
             "date": "2026-06-10", "provider": "ashby"},    # within window
        ]
        items = normalize.normalize_source_items("jobs", raw, "2026-05-17", "2026-06-16")
        titles = [i.title for i in items]
        self.assertIn("Founding Research Scientist", titles)  # not dropped by date window
        self.assertEqual(2, len(items))


class JobsDedupeTests(unittest.TestCase):
    def test_jobs_dedupe_by_url_not_fuzzy_boilerplate(self):
        from lib import dedupe, schema
        # Distinct roles sharing heavy boilerplate but with unique URLs.
        boiler = "TL;DR: Listen Labs is a fast-growing early-stage startup. Roll up your sleeves."
        items = [
            schema.SourceItem(item_id=f"AB{i}", source="jobs", title=t, body=boiler,
                              url=f"https://jobs.ashbyhq.com/listenlabs/{i}")
            for i, t in enumerate(["Founding Research Scientist", "Account Executive",
                                   "Growth Associate", "Business Development Rep"])
        ]
        kept = dedupe.dedupe_items(items)
        self.assertEqual(4, len(kept))  # all distinct URLs survive

    def test_jobs_dedupe_collapses_same_url(self):
        from lib import dedupe, schema
        items = [
            schema.SourceItem(item_id="AB1", source="jobs", title="Role", body="x",
                              url="https://jobs.ashbyhq.com/listenlabs/1"),
            schema.SourceItem(item_id="AB1b", source="jobs", title="Role", body="x",
                              url="https://jobs.ashbyhq.com/listenlabs/1"),
        ]
        self.assertEqual(1, len(dedupe.dedupe_items(items)))
