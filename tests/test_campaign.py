"""Campaign rules: schema, evidence check, approval, brief hash, draft gate, CLI.

Uses only the fictional fixture in docs/examples/. The LLM is replaced by a recorded fake
response (tests/fixtures/example_draft.json); no paid API calls.
"""

import copy
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from content_clipper import llm
from content_clipper.campaign import store
from content_clipper.campaign.draft import (
    RulesDraft,
    add_campaign,
    has_literal,
    norm,
    number_in,
    verify_draft,
)
from content_clipper.cli import app
from content_clipper.models import CampaignRules, CampaignRulesBody

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "docs" / "examples"
BRIEF = (EXAMPLES / "brief.example.md").read_text(encoding="utf-8")
DRAFT_JSON = json.loads((REPO / "tests" / "fixtures" / "example_draft.json").read_text())
NAME = "acme-launch-trailer"


def example_rules_data() -> dict:
    return yaml.safe_load((EXAMPLES / "rules.example.yaml").read_text(encoding="utf-8"))


def fake_draft(mutate=None) -> RulesDraft:
    data = copy.deepcopy(DRAFT_JSON)
    if mutate:
        mutate(data)
    return RulesDraft.model_validate(data)


def set_evidence(data: dict, path: str, quote: str | None) -> None:
    data["evidence"] = [e for e in data["evidence"] if e["path"] != path]
    if quote is not None:
        data["evidence"].append({"path": path, "quote": quote})


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A repo-like cwd with campaigns/_template and the LLM replaced by the recorded fake."""
    shutil.copytree(REPO / "campaigns" / "_template", tmp_path / "campaigns" / "_template")
    shutil.copytree(REPO / "config", tmp_path / "config")
    brief = tmp_path / "pasted_brief.md"
    brief.write_bytes((EXAMPLES / "brief.example.md").read_bytes())
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_parse(**kw):
        calls.append(kw)
        return fake_draft()

    monkeypatch.setattr(llm, "parse_structured", fake_parse)
    return {"root": tmp_path, "brief": brief, "calls": calls}


def cli(*args, input=None):
    return CliRunner().invoke(app, list(args), input=input)


# ---------------------------------------------------------------- schema


def test_example_rules_are_valid():
    rules = CampaignRules.model_validate(example_rules_data())
    assert rules.status == "reviewed"
    assert rules.audio.music_allowed is False
    assert rules.caption.required_lines[0].options == ["Out now on all platforms"]
    assert store.problems(rules) == []


def test_template_is_a_draft_with_model_defaults():
    data = yaml.safe_load((REPO / "campaigns" / "_template" / "rules.yaml").read_text())
    rules = CampaignRules.model_validate(data)
    assert rules.status == "draft"
    assert rules.body() == CampaignRulesBody()


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(status="approved"),
    lambda d: d.update(colour="red"),
    lambda d: d.update(platforms=["facebook"]),
    lambda d: d["video"].update(min_length_s=-1),
    lambda d: d["source"].update(policy="whatever"),
    lambda d: d["caption"]["required_lines"][0].update(where="sidebar"),
    lambda d: d["posting"].update(same_clip_across_platforms="maybe"),
    lambda d: d["audio"].update(music_allowed="sometimes"),
])
def test_invalid_rules_are_rejected(mutate):
    data = example_rules_data()
    mutate(data)
    with pytest.raises(ValidationError):
        CampaignRules.model_validate(data)


def test_problems_block_inconsistent_rules():
    data = example_rules_data()
    data["video"]["max_length_s"] = 5
    data["caption"]["disclosure"]["accepted_tags"] = []
    data["platforms"] = []
    found = store.problems(CampaignRules.model_validate(data))
    assert len(found) == 3


# ---------------------------------------------------------------- evidence check


def test_recorded_draft_verifies_completely():
    v = verify_draft(fake_draft(), BRIEF)
    assert v.rejections == []
    assert v.verified == 31
    assert v.body == CampaignRules.model_validate(example_rules_data()).body()
    assert v.evidence["audio.music_allowed"] == "No added music."


def test_hallucinated_quote_resets_value_to_null():
    v = verify_draft(fake_draft(lambda d: set_evidence(
        d, "audio.music_allowed", "Background music is not allowed.")), BRIEF)
    assert v.body.audio.music_allowed is None
    assert [r.path for r in v.rejections] == ["audio.music_allowed"]
    assert "audio.music_allowed" not in v.evidence
    assert any("Could not verify audio.music_allowed" in q for q in v.body.unknowns)


def test_missing_evidence_resets_value():
    v = verify_draft(fake_draft(lambda d: set_evidence(d, "audio.ai_voice_allowed", None)), BRIEF)
    assert v.body.audio.ai_voice_allowed is None
    assert v.rejections[0].reason == "no evidence quote given"


def test_number_must_appear_in_its_quote():
    def mutate(d):
        d["rules"]["video"]["min_length_s"] = 15  # real quote says 8
    v = verify_draft(fake_draft(mutate), BRIEF)
    assert v.body.video.min_length_s is None
    assert "15 does not appear" in v.rejections[0].reason


def test_unbacked_list_item_is_dropped_and_evidence_reindexed():
    def mutate(d):
        d["rules"]["prohibited"].insert(0, "No political content")
        d["evidence"] = [e for e in d["evidence"] if not e["path"].startswith("prohibited")]
        d["evidence"] += [
            {"path": "prohibited[0]", "quote": "Politics are off limits"},  # not in brief
            {"path": "prohibited[1]", "quote": "Exaggerated hooks, rage bait"},
            {"path": "prohibited[2]", "quote": "reposting another creator's video"},
        ]
    v = verify_draft(fake_draft(mutate), BRIEF)
    assert v.body.prohibited == ["exaggerated hooks, rage bait", "reposting another creator's video"]
    assert v.evidence["prohibited[0]"] == "Exaggerated hooks, rage bait"
    assert v.evidence["prohibited[1]"] == "reposting another creator's video"
    assert "prohibited[2]" not in v.evidence


def test_pasted_strings_must_be_verbatim_in_brief():
    def mutate(d):
        d["rules"]["caption"]["required_mentions"][0]["text"] = "@acme"  # prefix of @acmestudio
        d["rules"]["caption"]["required_lines"][0]["options"] = ["Available now everywhere"]
    v = verify_draft(fake_draft(mutate), BRIEF)
    assert v.body.caption.required_mentions == []
    assert v.body.caption.required_lines == []
    assert {r.path for r in v.rejections} == {"caption.required_mentions[0]",
                                              "caption.required_lines[0]"}


def test_dotted_index_paths_and_typographic_quotes_are_accepted():
    def mutate(d):
        set_evidence(d, "prohibited[1]", None)
        d["evidence"].append({"path": "prohibited.1", "quote": "Reposting another creator’s  video"})
    v = verify_draft(fake_draft(mutate), BRIEF)
    assert v.rejections == []


def test_helpers():
    assert norm("  Tag\n@Acme  “x” — y ") == 'tag @acme "x" - y'
    assert number_in("minimum 8 seconds", 8.0)
    assert not number_in("minimum 18 seconds", 8)
    assert not number_in("version 8.5", 8)
    assert number_in("up to three more hashtags", 3)
    assert has_literal(norm(BRIEF), "#Ad")
    assert not has_literal(norm(BRIEF), "#A")
    assert not has_literal(norm(BRIEF), "@acme")


# ---------------------------------------------------------------- store: add, approve, gate


def test_add_writes_verbatim_brief_and_draft_rules(workspace):
    root = workspace["root"] / "campaigns"
    rules, _ = add_campaign(NAME, workspace["brief"].read_bytes(), model="m", root=root)
    d = root / NAME
    assert (d / "brief.md").read_bytes() == workspace["brief"].read_bytes()
    assert rules.status == "draft" and rules.brief_sha256 == ""
    on_disk = store.read_rules(d / "rules.yaml")
    assert on_disk == rules
    assert (d / "rules.yaml").read_text().startswith("# Drafted by")
    kw = workspace["calls"][0]
    assert kw["model"] == "m" and kw["output_type"] is RulesDraft
    assert BRIEF in kw["user"] and "<template>" in kw["user"]


def test_add_refuses_to_overwrite_without_force(workspace):
    root = workspace["root"] / "campaigns"
    add_campaign(NAME, b"brief one", model="m", root=root)
    with pytest.raises(store.CampaignError, match="--force"):
        add_campaign(NAME, b"brief two", model="m", root=root)
    add_campaign(NAME, b"brief two", model="m", root=root, force=True)
    assert (root / NAME / "brief.md").read_bytes() == b"brief two"


@pytest.mark.parametrize("bad", ["Acme", "acme_launch", "../x", "", "-x"])
def test_campaign_names_are_slugs(bad):
    with pytest.raises(store.CampaignError):
        store.campaign_dir(bad)


def test_draft_is_gated_until_approved(workspace):
    root = workspace["root"] / "campaigns"
    add_campaign(NAME, workspace["brief"].read_bytes(), model="m", root=root)
    with pytest.raises(store.CampaignNotReviewed):
        store.require_reviewed(NAME, root)
    c = store.approve(NAME, root)
    assert c.status == "reviewed"
    assert c.rules.brief_sha256 == store.sha256_file(root / NAME / "brief.md")
    assert store.require_reviewed(NAME, root).status == "reviewed"


def test_changed_brief_reverts_status_to_draft(workspace):
    root = workspace["root"] / "campaigns"
    add_campaign(NAME, workspace["brief"].read_bytes(), model="m", root=root)
    store.approve(NAME, root)
    brief = root / NAME / "brief.md"
    brief.write_text(brief.read_text() + "\nNEW: music is now allowed.\n")

    c = store.load_campaign(NAME, root)
    assert c.rules.status == "reviewed"  # the file still says reviewed...
    assert c.status == "draft"  # ...but it is treated as draft
    assert any("brief.md changed" in w for w in c.warnings)
    with pytest.raises(store.CampaignNotReviewed, match="brief.md changed"):
        store.require_reviewed(NAME, root)

    store.approve(NAME, root)  # a human re-approves after checking the new brief
    assert store.require_reviewed(NAME, root).status == "reviewed"


def test_hand_edited_reviewed_status_is_not_trusted(workspace):
    root = workspace["root"] / "campaigns"
    add_campaign(NAME, workspace["brief"].read_bytes(), model="m", root=root)
    p = root / NAME / "rules.yaml"
    p.write_text(p.read_text().replace("status: draft", "status: reviewed"))
    c = store.load_campaign(NAME, root)
    assert c.status == "draft"
    assert any("brief_sha256 is empty" in w for w in c.warnings)


def test_approve_keeps_comments_and_human_edits(workspace):
    root = workspace["root"] / "campaigns"
    add_campaign(NAME, workspace["brief"].read_bytes(), model="m", root=root)
    p = root / NAME / "rules.yaml"
    p.write_text(p.read_text().replace("max_length_s: null", "max_length_s: 60  # my answer"))
    store.approve(NAME, root)
    text = p.read_text()
    assert "# my answer" in text and "# Drafted by" in text
    assert store.read_rules(p).video.max_length_s == 60


def test_approve_refuses_problems(workspace):
    root = workspace["root"] / "campaigns"
    add_campaign(NAME, workspace["brief"].read_bytes(), model="m", root=root)
    p = root / NAME / "rules.yaml"
    p.write_text(p.read_text().replace("max_length_s: null", "max_length_s: 5"))
    with pytest.raises(store.CampaignError, match="min_length_s"):
        store.approve(NAME, root)
    assert store.load_campaign(NAME, root).rules.status == "draft"


def test_expiry_only_warns(workspace):
    root = workspace["root"] / "campaigns"
    add_campaign(NAME, workspace["brief"].read_bytes(), model="m", root=root)
    p = root / NAME / "rules.yaml"
    text = p.read_text().replace("valid_until: null", "valid_until: '2020-01-01'")
    p.write_text(text.replace("expires: null", "expires: '2020-01-02'"))
    store.approve(NAME, root)
    c = store.load_campaign(NAME, root, today=date(2026, 1, 1))
    assert c.status == "reviewed"
    assert len(c.warnings) == 2


def test_checklist_sections_cover_posting_and_unknowns():
    rules = CampaignRules.model_validate(example_rules_data())
    s = store.checklist_sections(rules)
    assert "Likes must be visible" in s["Human checks (code cannot control these)"]
    posting = s["Posting"]
    assert "Keep every post live for at least 14 days" in posting
    assert "Post the same clip at most 3 times" in posting
    assert any(line.startswith("Unknown whether the same clip on multiple") for line in posting)
    assert s["Open questions for the campaign team"] == rules.unknowns


# ---------------------------------------------------------------- CLI


def test_cli_add_show_approve_checklist(workspace):
    r = cli("campaign", "add", NAME, "--brief", str(workspace["brief"]))
    assert r.exit_code == 0, r.output
    assert "status: draft" in r.output and "Rejected: 0" in r.output
    assert "campaign approve acme-launch-trailer" in r.output

    r = cli("campaign", "show", NAME)
    assert r.exit_code == 0, r.output
    assert "status    draft" in r.output and "@acmestudio" in r.output

    r = cli("checklist", NAME)
    assert r.exit_code == 0, r.output
    assert "[ ] Likes must be visible" in r.output

    r = cli("campaign", "approve", NAME)
    assert r.exit_code == 0, r.output
    assert "Approved" in r.output
    assert "status    reviewed" in cli("campaign", "show", NAME).output


def test_cli_add_reads_stdin(workspace):
    r = cli("campaign", "add", NAME, "--brief", "-", input=BRIEF)
    assert r.exit_code == 0, r.output
    assert (workspace["root"] / "campaigns" / NAME / "brief.md").read_text() == BRIEF


def test_cli_add_reports_llm_failure_and_writes_nothing(workspace, monkeypatch):
    def boom(**kw):
        raise llm.LLMError("Anthropic authentication failed: check ANTHROPIC_API_KEY in .env")

    monkeypatch.setattr(llm, "parse_structured", boom)
    r = cli("campaign", "add", NAME, "--brief", str(workspace["brief"]))
    assert r.exit_code == 1
    assert "authentication failed" in r.output
    assert not (workspace["root"] / "campaigns" / NAME).exists()


def test_cli_errors_are_clean(workspace):
    assert cli("campaign", "show", "nope").exit_code == 1
    assert cli("campaign", "add", "Bad_Name", "--brief", str(workspace["brief"])).exit_code == 1
    assert cli("campaign", "add", NAME, "--brief", "missing.md").exit_code == 1
