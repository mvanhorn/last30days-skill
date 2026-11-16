"""Named teams read from a board's own titles (lib/jobs_teams.py), the digest
slots they take, and how the digest shows them."""

from __future__ import annotations

from lib import jobs_digest, jobs_teams, render

WINDOW_START = "2026-10-17"


def _role(title, date="2026-11-10", location="Seattle, WA", dated=True, index=[0]):
    index[0] += 1
    return {
        "title": title, "department": "", "location": location,
        "date": date, "posted_date_true": dated,
        "url": f"https://jobs.example.com/{index[0]}",
    }


def _board():
    # A large board: hundreds of store roles, a long-running AWS team, and a
    # new team whose name no other title carries.
    rows = [_role(f"Store Associate, Store {n}", date="2026-09-01") for n in range(200)]
    rows += [_role(f"Software Engineer, AWS Billing {n}", date="2026-06-01") for n in range(5)]
    rows += [_role(f"Area Manager, Seattle", date="2026-11-01") for _ in range(4)]
    rows += [
        _role("Applied Scientist, Fauna"),
        _role("Robotics Software Engineer, Fauna", date="2026-11-12"),
        _role("Marketing Manager, Fauna Robotics", date="2026-11-11"),
        _role("Hardware Engineer, Fauna", date="2026-10-20"),
    ]
    rows += [
        _role("Head of Alternate Payment Methods, Example Agentic Payments", date="2026-11-14"),
        _role("Administrative Assistant to the GM, Agentic Payments Ops", date="2026-11-15"),
    ]
    return rows


def test_a_new_team_named_only_in_its_own_titles_ranks_first_and_stores_and_places_are_not_teams():
    result = jobs_teams.named_teams(_board(), company="Example", window_start=WINDOW_START)

    names = [team["name"] for team in result["teams"]]
    assert names[0] == "fauna"
    assert result["teams"][0] == {"name": "fauna", "roles": 4, "new": 4}
    # "fauna robotics" is the same team, not a second one.
    assert "fauna robotics" not in names
    assert not any("store" in name or name == "seattle" for name in names)
    assert result["team_items"][0]["title"] == "Robotics Software Engineer, Fauna"


def test_a_head_of_a_team_no_other_title_names_is_a_lead_and_an_assistant_is_not():
    result = jobs_teams.named_teams(_board(), company="Example", window_start=WINDOW_START)

    leads = [row["title"] for row in result["lead_items"]]
    assert "Head of Alternate Payment Methods, Example Agentic Payments" in leads
    assert not any("Assistant" in title for title in leads)


def test_an_undated_board_still_names_its_teams_and_leads_without_counting_new_roles():
    rows = [dict(row, posted_date_true=False) for row in _board()]
    result = jobs_teams.named_teams(rows, company="Example", window_start=WINDOW_START)

    assert result["teams"][0] == {"name": "fauna", "roles": 4, "new": None}
    assert result["lead_items"]


def test_named_team_roles_and_leads_take_digest_slots_and_are_tagged():
    rows = _board()
    teams = jobs_teams.named_teams(rows, company="Example", window_start=WINDOW_START)
    selected = jobs_digest.select_roles(rows, open_roles=len(rows), teams=teams, limit=5)
    digest = jobs_digest.build_digest(rows, selected)

    tagged = {entry["title"]: entry for entry in digest["roles"]}
    lead = tagged["Head of Alternate Payment Methods, Example Agentic Payments"]
    assert lead["team_lead"] is True
    assert tagged["Robotics Software Engineer, Fauna"]["team"] == {"name": "fauna", "roles": 4}


def test_the_named_teams_line_shows_counts_and_new_roles_only_when_dated():
    line = render._named_teams_line([
        {"name": "fauna", "roles": 17, "new": 16},
        {"name": "public sector", "roles": 40, "new": None},
    ])
    assert line == (
        "- Named teams (team names repeated across this board's titles): "
        "fauna (17 roles, 16 new); public sector (40 roles)."
    )
    assert render._named_teams_line([]) == ""
    assert render._named_teams_line(None) == ""


def test_one_role_posted_in_three_cities_is_one_role_not_a_team():
    rows = _board() + [
        _role("Program Manager, Lonely Team", location=city) for city in ("Austin, TX", "Boston, MA", "Denver, CO")
    ]
    result = jobs_teams.named_teams(rows, company="Example", window_start=WINDOW_START)

    assert "lonely team" not in [team["name"] for team in result["teams"]]
