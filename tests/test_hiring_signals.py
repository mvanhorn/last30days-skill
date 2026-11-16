import unittest

from lib import hiring_signals, pipeline, schema


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
    def test_theme_keywords_match_whole_words_only(self):
        themed = hiring_signals._theme_items([
            job("JP-Technical Specialist", "Apple Retail store training and maintaining displays", "Apple Retail"),
            job("Web Developer", "html and css", "Engineering"),
            job("Staff Engineer, LLMs", "train large models", "Engineering"),
        ])
        ai = [item.title for item in themed.get("ai and machine learning", [])]
        self.assertEqual(["Staff Engineer, LLMs"], ai)

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

    def _located(self, *rows):
        items = []
        for title, location in rows:
            item = job(title, "work", "Engineering")
            item.metadata["location"] = location
            items.append(item)
        return hiring_signals.analyze(items, explicit=True, topic="Acme")["strategic_candidates"]

    def test_a_spelling_of_a_place_already_on_the_board_is_not_a_new_geography(self):
        candidates = self._located(
            ("Engineer A", "San Francisco"),
            ("Engineer B", "San Francisco"),
            ("Engineer C", "San Francisco"),
            ("Engineer D", "Seattle"),
            ("Engineer E", "Seattle, WA"),
        )
        self.assertFalse([c for c in candidates if "new-geo" in c["flags"]])

    def test_a_title_tail_is_specialized_only_when_it_names_a_specialty(self):
        candidates = self._located(
            ("Engineer, Payments", "Seattle"),
            ("Engineer, Seattle", "Seattle"),
            ("Engineer, Multiple Teams", "Seattle"),
            ("Engineer, Entry Level", "Seattle"),
        )
        flagged = {c["title"] for c in candidates if "specialized" in c["flags"]}
        self.assertEqual({"Engineer, Payments"}, flagged)


# Company text a board repeats on every role, the way Microsoft's postings do.
BOILERPLATE = (
    "Our team accelerates customer outcomes and business growth with AI-powered "
    "solutions and cloud platforms. We design for security and data insights."
)
KEYWORD_THEMES = set(hiring_signals.THEME_KEYWORDS)


def board(*roles: tuple[str, str, str]) -> list[schema.SourceItem]:
    """Postings that each open with BOILERPLATE, then the role's own text."""
    return [job(title, f"{title}\n{department}\n{BOILERPLATE}\n{own}", department)
            for title, own, department in roles]


def themes_of(themed: dict[str, list[schema.SourceItem]], title: str) -> set[str]:
    return {theme for theme, items in themed.items() if any(i.title == title for i in items)}


class BoilerplateThemeTests(unittest.TestCase):
    def test_role_matching_only_shared_boilerplate_is_unthemed(self):
        themed = hiring_signals._theme_items(board(
            ("Logistics Technician", "Move parts between sites.", "Operations"),
            ("Paralegal", "Draft contracts.", "Legal"),
            ("Facilities Coordinator", "Book rooms.", "Workplace"),
            ("Product Designer", "Shape the editor.", "Studio"),
        ))
        for title in ("Logistics Technician", "Paralegal", "Facilities Coordinator"):
            self.assertEqual(set(), themes_of(themed, title) & KEYWORD_THEMES, title)
        self.assertEqual({"product expansion"}, themes_of(themed, "Product Designer") & KEYWORD_THEMES)
        self.assertIn("operations", themed)

    def test_role_unique_body_sentence_still_themes(self):
        themed = hiring_signals._theme_items(board(
            ("Software Engineer", "You will build LLM evaluation pipelines.", "Engineering"),
            ("Paralegal", "Draft contracts.", "Legal"),
            ("Facilities Coordinator", "Book rooms.", "Workplace"),
        ))
        self.assertEqual({"ai and machine learning"},
                         themes_of(themed, "Software Engineer") & KEYWORD_THEMES)

    def test_boilerplate_needs_three_roles_before_it_is_removed(self):
        themed = hiring_signals._theme_items(board(
            ("Paralegal", "Draft contracts.", "Legal"),
            ("Facilities Coordinator", "Book rooms.", "Workplace"),
        ))
        # Two roles sharing a sentence is not yet company text.
        self.assertIn("ai and machine learning", themes_of(themed, "Paralegal"))

    def test_title_match_outranks_body_match(self):
        themed = hiring_signals._theme_items([
            job("Software Engineer", "Software Engineer\nYou will tune ML ranking.", "Engineering"),
            job("Machine Learning Engineer", "Machine Learning Engineer\nShip things.", "Engineering"),
            job("Data Engineer", "Data Engineer\nKeep pipelines up.", "AI Platform"),
        ])
        self.assertEqual(
            ["Machine Learning Engineer", "Data Engineer", "Software Engineer"],
            [item.title for item in themed["ai and machine learning"]],
        )

    def test_generic_words_do_not_theme_a_logistics_technician(self):
        themed = hiring_signals._theme_items([
            job(
                "Logistics Technician",
                "Logistics Technician\nSupport warehouse growth, design shipping solutions on "
                "our platform, and model activation insights for admin teams.",
                "Operations",
            ),
        ])
        self.assertEqual({"operations"}, set(themed))

    def test_data_center_roles_are_infrastructure_not_data(self):
        themed = hiring_signals._theme_items([
            job("Data Center Program Manager", "Data Center Program Manager", "Operations"),
            job("Data Analyst", "Data Analyst", "Finance"),
        ])
        self.assertEqual({"infrastructure and reliability"},
                         themes_of(themed, "Data Center Program Manager") & KEYWORD_THEMES)
        self.assertEqual({"data and analytics"}, themes_of(themed, "Data Analyst") & KEYWORD_THEMES)


class DistinctEvidenceTests(unittest.TestCase):
    def _items(self) -> list[schema.SourceItem]:
        items = []
        for prefix, department in (("Sales Lead", "Sales"), ("ML Engineer", "Research"),
                                   ("Cloud Engineer", "Infra")):
            items.extend(job(f"{prefix} {n}", f"{prefix} {n}", department) for n in range(3))
        # Each of these matches all three themes on its title, and comes first.
        shared = [job(f"Sales ML Cloud Architect {n}", "Sales ML Cloud Architect", "Office")
                  for n in range(2)]
        return shared + items

    def test_no_role_is_an_example_under_two_themes(self):
        summary = hiring_signals.analyze(self._items(), explicit=True, topic="Acme")
        by_theme = {s["theme"]: s for s in summary["signals"]}
        self.assertTrue({"go-to-market", "ai and machine learning",
                         "infrastructure and reliability"} <= set(by_theme))
        urls = [e["url"] for s in summary["signals"] for e in s["evidence"]]
        self.assertEqual(len(urls), len(set(urls)))
        for theme in ("go-to-market", "ai and machine learning", "infrastructure and reliability"):
            self.assertTrue(by_theme[theme]["evidence"], theme)

    def test_evidence_count_is_unchanged_by_dedup(self):
        summary = hiring_signals.analyze(self._items(), explicit=True, topic="Acme")
        by_theme = {s["theme"]: s for s in summary["signals"]}
        for theme in ("go-to-market", "ai and machine learning", "infrastructure and reliability"):
            self.assertEqual(5, by_theme[theme]["evidence_count"], theme)

    def test_every_theme_gets_a_first_example_before_any_gets_a_second(self):
        summary = hiring_signals.analyze(self._items(), explicit=True, topic="Acme")
        firsts = [s["evidence"][0]["title"] for s in summary["signals"][:3]]
        # The two all-theme roles go to the top two themes; the third takes its own.
        self.assertEqual(2, sum(title.startswith("Sales ML Cloud Architect") for title in firsts))


class HiringSignalGateTests(unittest.TestCase):
    def _bundle(self, items: list[schema.SourceItem]) -> schema.RetrievalBundle:
        bundle = schema.RetrievalBundle()
        bundle.add_items("primary", "jobs", items)
        return bundle

    def test_mega_cap_standard_run_with_real_concentration_passes_the_gate(self):
        roles = [
            (f"{level} Machine Learning Engineer, {area}", f"Train {area} models.", dept)
            for level, area, dept in [
                ("Senior", "Copilot", "Research"), ("Principal", "Azure AI", "AI Platform"),
                ("Staff", "Bing", "Search"), ("Senior", "Speech", "Research"),
                ("Principal", "Vision", "AI Platform"), ("Senior", "Ads", "Search"),
                ("Staff", "Office", "Research"), ("Senior", "Gaming", "AI Platform"),
                ("Principal", "Security", "Search"), ("Senior", "Health", "Research"),
            ]
        ] + [
            ("Logistics Technician", "Move parts.", "Operations"),
            ("Paralegal", "Draft contracts.", "Legal"),
        ]
        bundle = self._bundle(board(*roles))

        summary = pipeline._apply_hiring_signal_gate(bundle, explicit=False, topic="Microsoft")

        self.assertEqual("mega-cap", summary["company_size_tier"])
        self.assertTrue(summary["include"])
        self.assertEqual("ai and machine learning", summary["signals"][0]["theme"])
        self.assertIn("jobs", bundle.items_by_source)
        shown = [e["title"] for s in summary["signals"] for e in s["evidence"]]
        self.assertNotIn("Logistics Technician", shown)

    def test_mega_cap_board_themed_only_by_boilerplate_leaves_the_pool(self):
        # Before boilerplate removal, every one of these themed as AI, go-to-market,
        # infrastructure, product, enterprise and data.
        roles = [(f"Technician {n}", f"Move parts in bay {n}.", f"Site {n}") for n in range(12)]
        bundle = self._bundle(board(*roles))

        summary = pipeline._apply_hiring_signal_gate(bundle, explicit=False, topic="Microsoft")

        self.assertFalse(summary["include"])
        self.assertNotIn("jobs", bundle.items_by_source)


if __name__ == "__main__":
    unittest.main()


class GreenhouseParagraphsTest(unittest.TestCase):
    """HTML paragraphs without ending punctuation still split company from role text."""

    def test_unpunctuated_company_paragraph_themes_nothing(self):
        from lib import jobs, normalize

        roles = [
            ("Logistics Technician", "Move parts between sites"),
            ("Paralegal", "Draft contracts for review"),
            ("Executive Chef", "Run the kitchen line"),
            ("Office Coordinator", "Plan team events"),
        ]
        items = []
        for index, (title, duty) in enumerate(roles):
            raw = jobs._greenhouse_job_to_item({
                "id": index + 1,
                "title": title,
                "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{index + 1}",
                "content": f"<p>We build cloud infrastructure</p><p>{duty}</p>",
                "departments": [{"name": "Operations"}],
            }, "acme")
            items.append(normalize._normalize_jobs("jobs", raw, index, "2026-10-16", "2026-11-15"))

        themed = hiring_signals._theme_items(items)

        for title, _duty in roles:
            self.assertNotIn("infrastructure and reliability", themes_of(themed, title))
