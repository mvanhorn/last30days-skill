import unittest

from lib import normalize


class HiringSignalsNormalizeTests(unittest.TestCase):
    def test_normalize_jobs_source_item(self):
        items = normalize.normalize_source_items(
            "jobs",
            [
                {
                    "id": "J1",
                    "title": "Enterprise Security Engineer",
                    "description": "Build SSO and SOC 2 controls.",
                    "url": "https://example.com/jobs/1",
                    "department": "Engineering",
                    "location": "Remote",
                    "date": "2026-06-01",
                    "provider": "greenhouse",
                }
            ],
            "2026-05-16",
            "2026-06-16",
        )
        self.assertEqual(1, len(items))
        item = items[0]
        self.assertEqual("jobs", item.source)
        self.assertEqual("Enterprise Security Engineer", item.title)
        self.assertEqual("Engineering", item.container)
        self.assertEqual("greenhouse", item.author)
        self.assertEqual("Remote", item.metadata["location"])


if __name__ == "__main__":
    unittest.main()
