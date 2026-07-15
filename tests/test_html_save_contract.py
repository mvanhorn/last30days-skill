"""Contract tests for the /last30days HTML save handoff."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"
SAVE_HTML = ROOT / "skills" / "last30days" / "references" / "save-html-brief.md"


def test_skill_routes_html_to_reference_and_artifact_handoff():
    text = SKILL_MD.read_text(encoding="utf-8")
    start = text.index("## SHAREABLE HTML BRIEF")
    end = text.index("## WAIT FOR USER'S RESPONSE", start)
    section = text[start:end]

    assert "Read `references/save-html-brief.md`" in section
    assert "artifact handoff" in section
    assert "open the local file when the host can do so" in section
    assert "Upload or publish" in section
    assert "Append the confirmation line" not in section
    assert "a literal flag is not required" in section
    assert "do not confuse it with the complete Python CLI contract" in section


def test_html_deliverable_is_artifact_first_not_full_markdown_repeat():
    text = SAVE_HTML.read_text(encoding="utf-8")
    assert "**HTML as the requested deliverable**" in text
    assert 'prose like "give it to me in HTML"' in text
    assert "the HTML artifact is the primary output" in text
    assert "use the exact synthesis draft you prepared for" in text
    assert "Do not paste it to chat first" in text
    assert "the chat handoff is the single user-visible" in text
    assert "printf '📎 Shareable brief saved to %s\\n'" not in text
    assert 'echo "📎 Shareable brief saved to $HTML_PATH"' not in text
    assert "do **not** paste the full Markdown report back into chat" in text
    assert "The user asked for an HTML deliverable" in text
    assert "What do you want to do next?" in text
    assert "1. Open HTML file" in text
    assert "3. Done for now" in text


def test_html_handoff_opens_locally_without_os_command_menu():
    text = SAVE_HTML.read_text(encoding="utf-8")
    assert "Let the host choose the correct OS-specific mechanism" in text
    assert "do not print a menu of shell commands" in text
    assert "If opening fails or the host is headless" in text
    assert 'xdg-open "<absolute HTML path>"' not in text
    assert 'start "" "<absolute HTML path>"' not in text


def test_html_save_flow_does_not_publish_or_upload():
    text = SAVE_HTML.read_text(encoding="utf-8")
    assert "Do not upload in this flow unless the user chooses a publishing option" in text
    assert "Do NOT publish, upload, or send the HTML to a third-party service" in text
    assert "Do NOT block a local HTML export on a hosting decision" in text
    assert "Show the saved path and next-step choices first" in text


def test_markdown_and_html_access_paths_are_separate():
    text = SKILL_MD.read_text(encoding="utf-8")
    start = text.index("**Saved artifact access flow:**")
    section = text[start:start + 1600]

    assert "**Markdown file requested:**" in section
    assert "Do not offer hosted publishing for Markdown" in section
    assert "**HTML file requested:**" in section
    assert "show the absolute path" in section
    assert "open the HTML file, publish to an available/preferred HTML publishing service, or done for now" in section


def test_follow_up_turn_preserves_html_deliverable_mode():
    text = SAVE_HTML.read_text(encoding="utf-8")
    assert "explicitly refers back to that visible synthesis" in text
    assert "treat it as HTML-as-deliverable mode" in text
    assert "Always report whichever path the redirect actually used in the chat handoff" in text
