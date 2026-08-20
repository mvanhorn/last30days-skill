import unittest

from lib import hiring_signals, schema


def job(title: str, body: str, department: str = "Engineering") -> schema.SourceItem:
    return schema.SourceItem(
        item_id=title,
        source="jobs",
        title=title,
        body=body,
        url=f"https://example.com/jobs/{title.replace(' ', '-').lower()}",
        container=department,
        published_at="2026-06-01",
        date_confidence="high",
        metadata={"department": department},
    )


class HiringSignalsTests(unittest.TestCase):
    def test_startup_cluster_surfaces_in_standard_mode(self):
        items = [
            job("Founding Enterprise Solutions Engineer", "SSO SOC 2 procurement enterprise", "Sales"),
            job("Security Platform Engineer", "enterprise security audit admin", "Engineering"),
        ]
        summary = hiring_signals.analyze(items, explicit=False, topic="Listen Labs")
        self.assertTrue(summary["include"])
        self.assertEqual("startup", summary["company_size_tier"])
        self.assertEqual("enterprise readiness", summary["signals"][0]["theme"])

    def test_mega_cap_scattered_roles_do_not_surface_standard_mode(self):
        items = [
            job("Retail Associate", "store operations", "Retail"),
            job("iOS Engineer", "mobile app", "Engineering"),
            job("Finance Analyst", "planning", "Finance"),
        ]
        summary = hiring_signals.analyze(items, explicit=False, topic="Apple")
        self.assertFalse(summary["include"])
        self.assertEqual("mega-cap", summary["company_size_tier"])
        self.assertIn("too diffuse", summary["omitted_reason"])

    def test_fortune_500_customer_boilerplate_does_not_make_startup_large_enterprise(self):
        items = [
            job(
                "Founding Enterprise Solutions Engineer",
                "Help Fortune 500 customers adopt SSO, SOC 2, and procurement workflows.",
                "Sales",
            ),
            job(
                "Security Platform Engineer",
                "Build enterprise security, audit, and admin workflows for Fortune 500 customers.",
                "Engineering",
            ),
        ]
        summary = hiring_signals.analyze(items, explicit=False, topic="Listen Labs")
        self.assertEqual("startup", summary["company_size_tier"])
        self.assertTrue(summary["include"])

    def test_explicit_mode_keeps_low_confidence_signal(self):
        items = [job("Customer Success Manager", "support enterprise customers", "Success")]
        summary = hiring_signals.analyze(items, explicit=True, topic="Acme")
        self.assertTrue(summary["include"])
        self.assertEqual("low", summary["signals"][0]["confidence"])

    def test_founding_specialized_role_surfaces_as_strategic_candidate(self):
        # The exact 2026-06-15 miss: one strategic role amid many generic ones.
        items = [
            job("Software Engineer", "backend services", "Engineering"),
            job("Software Engineer, Backend", "platform", "Engineering"),
            job("Software Engineer, Frontend", "ui", "Engineering"),
            job("Founding Research Scientist, Human Simulation", "simulate users", "Research"),
        ]
        summary = hiring_signals.analyze(items, explicit=True, topic="Listen Labs")
        titles = [c["title"] for c in summary["strategic_candidates"]]
        self.assertIn("Founding Research Scientist, Human Simulation", titles)
        top = summary["strategic_candidates"][0]
        self.assertEqual("Founding Research Scientist, Human Simulation", top["title"])
        self.assertIn("founding", top["flags"])
        self.assertIn("specialized", top["flags"])

    def test_specialization_ignores_generic_level_qualifiers(self):
        self.assertEqual("", hiring_signals._specialization("Engineer, Senior"))
        self.assertEqual("Human Simulation", hiring_signals._specialization("Research Scientist, Human Simulation"))
        self.assertEqual("Forward Deployed", hiring_signals._specialization("Engineer (Forward Deployed)"))

    def test_new_geo_flag_for_rare_location(self):
        def loc_job(title, location):
            it = job(title, "work", "Engineering")
            it.metadata["location"] = location
            return it
        items = [
            loc_job("Engineer A", "San Francisco"),
            loc_job("Engineer B", "San Francisco"),
            loc_job("Engineer C", "San Francisco"),
            loc_job("Growth Lead", "New York"),
        ]
        summary = hiring_signals.analyze(items, explicit=True, topic="Acme")
        ny = [c for c in summary["strategic_candidates"] if c["location"] == "new york"]
        self.assertTrue(ny)
        self.assertIn("new-geo", ny[0]["flags"])


if __name__ == "__main__":
    unittest.main()
