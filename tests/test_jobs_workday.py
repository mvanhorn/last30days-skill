import threading
import unittest
from unittest.mock import patch

from lib import jobs, jobs_digest

TOKEN = "acme.wd5/AcmeCareers"
SITE = "https://www.acme.com"
CAREERS = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers">Jobs</a>'
LIST_URL = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/jobs"


def _row(index: int) -> dict:
    return {
        "title": f"Role {index}",
        "externalPath": f"/job/Remote/Role-{index}_JR{index}",
        "locationsText": "2 Locations",
        "postedOn": "Posted 3 Days Ago",
        "bulletFields": [f"JR{index}"],
    }


class FakeBoard:
    """Stands in for a Workday board at the `http.post` / `http.get` seam."""

    def __init__(self, size: int, *, reported_total: int | None = None, malformed: set[int] = frozenset()):
        self.size = size
        self.reported_total = size if reported_total is None else reported_total
        self.malformed = set(malformed)
        self.list_calls: list[tuple[str, dict]] = []
        self.detail_calls: list[str] = []
        self.failing_details: set[str] = set()
        self.empty_details: set[str] = set()
        self._lock = threading.Lock()

    def post(self, url, json_data, headers=None, **kwargs):
        with self._lock:
            self.list_calls.append((url, dict(json_data)))
        offset = json_data["offset"]
        rows = []
        for index in range(offset, min(offset + json_data["limit"], self.size)):
            row = _row(index)
            if index in self.malformed:
                del row["title"]
            rows.append(row)
        # Workday reports the total on the first page only.
        return {"total": self.reported_total if offset == 0 else 0, "jobPostings": rows, "facets": []}

    def get(self, url, headers=None, **kwargs):
        with self._lock:
            self.detail_calls.append(url)
        ident = url.rsplit("_", 1)[-1]
        if ident in self.failing_details:
            raise jobs.http.HTTPError("server error", status_code=500)
        if ident in self.empty_details:
            return {"errorCode": "HTTP_404"}
        return {
            "jobPostingInfo": {
                "jobDescription": f"<p>Own the {ident} roadmap.</p>",
                "startDate": "2026-11-03",
                "location": "Remote",
            }
        }


class WorkdayDetectionTests(unittest.TestCase):
    def test_careers_link_yields_provider_and_endpoint_parts(self):
        html = '<a href="https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite">Jobs</a>'
        provider, token = jobs.detect_ats(html)
        self.assertEqual("workday", provider)
        self.assertEqual("nvidia.wd5/NVIDIAExternalCareerSite", token)
        self.assertEqual(
            "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs",
            jobs.workday_list_url(token),
        )

    def test_locale_segment_is_not_taken_for_the_site(self):
        html = '<a href="https://acme.wd12.myworkdayjobs.com/en-US/AcmeCareers/job/Remote/x_JR1">Role</a>'
        provider, token = jobs.detect_ats(html)
        self.assertEqual("workday", provider)
        self.assertEqual("acme.wd12/AcmeCareers", token)
        self.assertEqual(
            "https://acme.wd12.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/jobs",
            jobs.workday_list_url(token),
        )

    def test_page_linking_only_a_workday_board_counts_as_a_careers_page(self):
        html = '<a href="https://acme.wd5.myworkdayjobs.com/AcmeCareers">Search</a>'
        self.assertTrue(jobs._looks_like_careers_html(html))


class WorkdayListingTests(unittest.TestCase):
    def _search(self, board, **kwargs):
        with patch("lib.jobs.http.post", side_effect=board.post):
            return jobs.search_workday_board(TOKEN, **kwargs)

    def test_45_row_board_is_read_across_three_list_calls(self):
        board = FakeBoard(45)
        items, info = self._search(board)
        self.assertEqual(3, len(board.list_calls))
        self.assertEqual({LIST_URL}, {url for url, _ in board.list_calls})
        self.assertEqual([0, 20, 40], sorted(body["offset"] for _, body in board.list_calls))
        self.assertEqual([f"Role {i}" for i in range(45)], [item["title"] for item in items])
        self.assertEqual({"board_total": 45, "board_capped": False, "board_cut_short": False}, info)

    def test_listing_rows_carry_no_posted_date_or_description(self):
        items, _ = self._search(FakeBoard(1))
        item = items[0]
        self.assertEqual("workday", item["provider"])
        self.assertEqual(TOKEN, item["board_token"])
        self.assertEqual("WDJR0", item["id"])
        self.assertEqual("https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/Remote/Role-0_JR0", item["url"])
        self.assertEqual("2 Locations", item["location"])
        self.assertEqual("", item["description"])
        self.assertIsNone(item["date"])
        self.assertEqual("low", item["date_confidence"])

    def test_board_over_the_ceiling_stops_there_and_records_the_cap(self):
        board = FakeBoard(2500)
        items, info = self._search(board)
        self.assertEqual(100, len(board.list_calls))
        self.assertEqual(2000, len(items))
        self.assertTrue(info["board_capped"])
        self.assertFalse(info["board_cut_short"])

    def test_403_or_429_on_the_list_is_a_miss(self):
        for status in (403, 429):
            with patch(
                "lib.jobs.http.post",
                side_effect=jobs.http.HTTPError("blocked", status_code=status),
            ):
                items, _info = jobs.search_workday_board(TOKEN)
            self.assertEqual([], items)

    @patch("lib.jobs.http.get")
    @patch("lib.jobs.http.post", side_effect=jobs.http.HTTPError("blocked", status_code=403))
    @patch("lib.jobs.http.get_text")
    def test_blocked_board_falls_through_to_the_normal_miss(self, mock_text, _mock_post, mock_get):
        mock_text.return_value = '<a href="https://acme.wd5.myworkdayjobs.com/AcmeCareers">Jobs</a>'
        items, artifact = jobs.search_jobs("Acme", ("2026-10-11", "2026-11-10"), {}, web_backend="none")
        self.assertEqual([], items)
        self.assertEqual("web", artifact["tier"])
        self.assertEqual(0, artifact["resultCount"])
        self.assertIn(f"careers:workday:{TOKEN}", artifact["attempted"])
        mock_get.assert_not_called()

    def test_rows_without_a_title_are_ignored(self):
        board = FakeBoard(25, malformed={3, 21})
        items, info = self._search(board)
        self.assertEqual(23, len(items))
        self.assertNotIn("", [item["title"] for item in items])
        self.assertFalse(info["board_cut_short"])

    def test_stops_fetching_when_the_budget_is_spent(self):
        board = FakeBoard(400)
        now = [0.0]

        def slow_post(url, json_data, headers=None, **kwargs):
            now[0] += 20.0  # every list call costs 20 s on the injected clock
            return board.post(url, json_data, headers, **kwargs)

        with patch("lib.jobs.http.post", side_effect=slow_post):
            items, info = jobs.search_workday_board(TOKEN, budget_seconds=30.0, clock=lambda: now[0])
        self.assertTrue(info["board_cut_short"])
        self.assertGreaterEqual(len(items), 20)
        self.assertLess(len(items), 400)
        # One page before the budget ran out, plus at most one in-flight call per worker.
        self.assertLessEqual(len(board.list_calls), 1 + jobs.WORKDAY_MAX_WORKERS)

    def test_a_failed_later_page_is_recorded_as_cut_short(self):
        board = FakeBoard(45)

        def flaky_post(url, json_data, headers=None, **kwargs):
            if json_data["offset"] == 20:
                raise jobs.http.HTTPError("server error", status_code=500)
            return board.post(url, json_data, headers, **kwargs)

        with patch("lib.jobs.http.post", side_effect=flaky_post):
            items, info = jobs.search_workday_board(TOKEN)
        self.assertEqual(25, len(items))
        self.assertTrue(info["board_cut_short"])


class WorkdayDetailTests(unittest.TestCase):
    def _items(self, board, count):
        with patch("lib.jobs.http.post", side_effect=board.post):
            items, _ = jobs.search_workday_board(TOKEN)
        return items[:count]

    def test_fills_description_and_posted_date_for_selected_roles_only(self):
        board = FakeBoard(5)
        items = self._items(board, 5)
        with patch("lib.jobs.http.get", side_effect=board.get):
            jobs.fetch_workday_details([items[1], items[3]])
        self.assertEqual(
            {
                "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/job/Remote/Role-1_JR1",
                "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/job/Remote/Role-3_JR3",
            },
            set(board.detail_calls),
        )
        self.assertEqual("Own the JR1 roadmap.", items[1]["description"])
        self.assertEqual("2026-11-03", items[1]["date"])
        self.assertEqual("high", items[1]["date_confidence"])
        for untouched in (items[0], items[2], items[4]):
            self.assertEqual("", untouched["description"])
            self.assertIsNone(untouched["date"])
            self.assertEqual("low", untouched["date_confidence"])

    def test_one_failed_detail_call_skips_only_that_role(self):
        board = FakeBoard(3)
        board.failing_details = {"JR1"}
        items = self._items(board, 3)
        with patch("lib.jobs.http.get", side_effect=board.get):
            jobs.fetch_workday_details(items)
        self.assertEqual("", items[1]["description"])
        self.assertIsNone(items[1]["date"])
        self.assertEqual("Own the JR0 roadmap.", items[0]["description"])
        self.assertEqual("2026-11-03", items[2]["date"])

    def test_no_detail_calls_once_the_budget_is_spent(self):
        board = FakeBoard(3)
        items = self._items(board, 3)
        with patch("lib.jobs.http.get", side_effect=board.get):
            jobs.fetch_workday_details(items, deadline=10.0, clock=lambda: 11.0)
        self.assertEqual([], board.detail_calls)
        self.assertEqual("", items[0]["description"])

    @patch("lib.jobs.http.get_text")
    def test_search_jobs_details_only_the_roles_the_digest_selected(self, mock_text):
        mock_text.return_value = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers">Jobs</a>'
        board = FakeBoard(45)
        with patch("lib.jobs.http.post", side_effect=board.post), \
                patch("lib.jobs.http.get", side_effect=board.get):
            items, artifact = jobs.search_jobs(
                "Acme", ("2026-10-11", "2026-11-10"), {}, web_backend="none", site=SITE,
            )
        self.assertEqual(45, len(items))
        self.assertEqual("ats", artifact["tier"])
        self.assertEqual("workday", artifact["provider"])
        self.assertEqual(TOKEN, artifact["board_token"])
        self.assertEqual(45, artifact["board_total"])
        self.assertFalse(artifact["board_capped"])
        self.assertFalse(artifact["board_cut_short"])
        selected = jobs_digest.DIGEST_MAX_ROLES
        self.assertEqual(selected, len(board.detail_calls))
        digest = artifact["digest"]
        self.assertEqual(selected, len(digest["roles"]))
        self.assertTrue(all(entry["text"].startswith("Own the JR") for entry in digest["roles"]))
        self.assertTrue(all(entry["posted"] == "2026-11-03" for entry in digest["roles"]))
        self.assertEqual(selected, sum(1 for item in items if item["description"]))
        self.assertNotIn("note", digest)
        # Listing rows carry no true date, so the board has no count of new roles.
        self.assertEqual(45, artifact["open_roles"])
        self.assertIsNone(artifact["new_in_window"])

    @patch("lib.jobs.http.get_text")
    def test_spent_budget_skips_detail_calls_and_the_digest_says_so(self, mock_text):
        mock_text.return_value = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers">Jobs</a>'
        board = FakeBoard(45)
        real = jobs.fetch_workday_details

        def late(items, **kwargs):
            return real(items, deadline=kwargs["deadline"], clock=lambda: kwargs["deadline"] + 1)

        with patch("lib.jobs.http.post", side_effect=board.post), \
                patch("lib.jobs.http.get", side_effect=board.get), \
                patch.object(jobs, "fetch_workday_details", side_effect=late):
            _items, artifact = jobs.search_jobs(
                "Acme", ("2026-10-11", "2026-11-10"), {}, web_backend="none", site=SITE,
            )
        self.assertEqual([], board.detail_calls)
        self.assertEqual(
            "role text fetched for 0 of 40 selected roles (time budget spent before 40)",
            artifact["digest"]["note"],
        )
        self.assertTrue(all(entry["text"] == "" for entry in artifact["digest"]["roles"]))

    def _search(self, board):
        with patch("lib.jobs.http.get_text", return_value=CAREERS), \
                patch("lib.jobs.http.post", side_effect=board.post), \
                patch("lib.jobs.http.get", side_effect=board.get):
            return jobs.search_jobs(
                "Acme", ("2026-10-11", "2026-11-10"), {}, web_backend="none", site=SITE,
            )

    def test_small_board_with_every_detail_fetched_counts_its_new_roles(self):
        _items, artifact = self._search(FakeBoard(5))
        self.assertEqual(5, artifact["open_roles"])
        self.assertEqual(5, artifact["new_in_window"])
        self.assertNotIn("note", artifact["digest"])

    def test_failed_and_empty_detail_calls_count_as_unfetched(self):
        board = FakeBoard(5)
        board.failing_details = {"JR1"}
        board.empty_details = {"JR3"}
        with patch("lib.jobs.http.post", side_effect=board.post):
            items, _ = jobs.search_workday_board(TOKEN)
        with patch("lib.jobs.http.get", side_effect=board.get):
            self.assertEqual((0, 2), jobs.fetch_workday_details(items))
        with patch("lib.jobs.http.get", side_effect=board.get):
            self.assertEqual(
                (5, 0), jobs.fetch_workday_details(items, deadline=10.0, clock=lambda: 11.0),
            )

        _items, artifact = self._search(board)
        self.assertEqual(
            "role text fetched for 3 of 5 selected roles (request failed or came back empty for 2)",
            artifact["digest"]["note"],
        )
        # Two roles have no true date, so the board has no count of new roles.
        self.assertIsNone(artifact["new_in_window"])

    def test_two_postings_with_one_title_both_get_their_details(self):
        board = FakeBoard(3)
        real_post = board.post

        def post(url, json_data, headers=None, **kwargs):
            payload = real_post(url, json_data, headers=headers, **kwargs)
            for row in payload["jobPostings"]:
                row["title"] = "Software Engineer"
            return payload

        board.post = post
        _items, artifact = self._search(board)
        self.assertEqual(3, len(board.detail_calls))
        self.assertEqual(
            {"Own the JR0 roadmap.", "Own the JR1 roadmap.", "Own the JR2 roadmap."},
            {entry["text"] for entry in artifact["digest"]["roles"]},
        )

    def test_board_on_a_guessed_domain_gives_counts_and_no_digest(self):
        board = FakeBoard(5)
        with patch("lib.jobs.http.get_text", return_value=CAREERS), \
                patch("lib.jobs.http.post", side_effect=board.post), \
                patch("lib.jobs.http.get", side_effect=board.get):
            _items, artifact = jobs.search_jobs(
                "Acme", ("2026-10-11", "2026-11-10"), {}, web_backend="none",
            )
        self.assertEqual(5, artifact["open_roles"])
        self.assertFalse(artifact["owned"])
        self.assertNotIn("digest", artifact)
        self.assertEqual([], board.detail_calls)

    def test_capped_board_counts_as_a_floor(self):
        page = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers">Jobs</a>'
        board = FakeBoard(2000, reported_total=2000)
        with patch("lib.jobs.http.get_text", return_value=page), \
                patch("lib.jobs.http.post", side_effect=board.post), \
                patch("lib.jobs.http.get", side_effect=board.get):
            _items, artifact = jobs.search_jobs(
                "Acme", ("2026-10-11", "2026-11-10"), {}, web_backend="none",
            )
        self.assertEqual(2000, artifact["open_roles"])
        self.assertTrue(artifact["open_roles_floor"])


if __name__ == "__main__":
    unittest.main()
