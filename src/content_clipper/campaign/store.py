"""campaigns/<name>/: brief.md (verbatim) + rules.yaml. Load, validate, gate on status, approve.

Nothing here knows about any particular campaign: every rule is data in rules.yaml.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from content_clipper.models import CampaignRules

CAMPAIGNS_DIR = Path("campaigns")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_TOP_ORDER = ("schema_version", "name", "status", "brief_file", "brief_sha256")


class CampaignError(RuntimeError):
    pass


class CampaignNotReviewed(CampaignError):
    pass


@dataclass
class Campaign:
    name: str
    dir: Path
    rules: CampaignRules
    status: str  # effective status: "reviewed" only if approved AND brief.md is unchanged
    warnings: list[str] = field(default_factory=list)

    @property
    def brief_path(self) -> Path:
        return self.dir / self.rules.brief_file


def campaign_dir(name: str, root: Path = CAMPAIGNS_DIR) -> Path:
    if not NAME_RE.match(name):
        raise CampaignError(
            f"invalid campaign name {name!r}: use lowercase letters, digits and dashes"
        )
    return root / name


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rules(path: Path) -> CampaignRules:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CampaignError(f"{path}: expected a YAML mapping")
    return CampaignRules.model_validate(data)


def dump_rules(rules: CampaignRules, header: str = "") -> str:
    data = rules.model_dump(mode="json")
    ordered = {k: data.pop(k) for k in _TOP_ORDER}
    evidence = data.pop("evidence")
    ordered.update(data)
    ordered["evidence"] = evidence
    return header + yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=100)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_rules(path: Path, rules: CampaignRules, header: str = "") -> None:
    _atomic_write(path, dump_rules(rules, header))


def expiry_warnings(rules: CampaignRules, today: date) -> list[str]:
    out = []
    if rules.valid_until and rules.valid_until < today:
        out.append(f"valid_until {rules.valid_until} has passed")
    for line in rules.caption.required_lines:
        if line.expires and line.expires < today:
            out.append(f"required line {line.id!r} expired on {line.expires}")
    return out


def load_campaign(name: str, root: Path = CAMPAIGNS_DIR, today: date | None = None) -> Campaign:
    d = campaign_dir(name, root)
    rules_path = d / "rules.yaml"
    if not rules_path.exists():
        raise CampaignError(
            f"{rules_path} not found; run `content-clipper campaign add {name} --brief <file>`"
        )
    rules = read_rules(rules_path)
    warnings: list[str] = []
    status = rules.status
    if rules.name != name:
        warnings.append(f"rules.yaml name is {rules.name!r} but the folder is {name!r}")
    brief = d / rules.brief_file
    if status == "reviewed":
        if not brief.exists():
            status = "draft"
            warnings.append(f"{brief} is missing: treated as draft")
        elif not rules.brief_sha256:
            status = "draft"
            warnings.append(
                "status says reviewed but brief_sha256 is empty (not set by `campaign approve`): "
                "treated as draft"
            )
        elif sha256_file(brief) != rules.brief_sha256:
            status = "draft"
            warnings.append(
                "brief.md changed since approval: treated as draft. Re-check rules.yaml against "
                f"the new brief, then run `content-clipper campaign approve {name}`"
            )
    warnings += expiry_warnings(rules, today or date.today())
    return Campaign(name=name, dir=d, rules=rules, status=status, warnings=warnings)


def require_reviewed(name: str, root: Path = CAMPAIGNS_DIR) -> Campaign:
    """Gate for every later stage: refuse to run on draft rules."""
    c = load_campaign(name, root)
    if c.status != "reviewed":
        detail = "; ".join(c.warnings) or "status is draft"
        raise CampaignNotReviewed(
            f"campaign {name!r} is not approved ({detail}). Review campaigns/{name}/rules.yaml "
            f"and run `content-clipper campaign approve {name}`"
        )
    return c


def problems(rules: CampaignRules) -> list[str]:
    """Consistency problems a draft may have but an approved campaign may not."""
    out = []
    if not rules.platforms:
        out.append("platforms is empty")
    v = rules.video
    if v.min_length_s is not None and v.max_length_s is not None and v.min_length_s > v.max_length_s:
        out.append(f"video.min_length_s {v.min_length_s} > video.max_length_s {v.max_length_s}")
    d = rules.caption.disclosure
    if d.required and not d.accepted_tags:
        out.append("caption.disclosure.required is true but accepted_tags is empty")
    ids = [line.id for line in rules.caption.required_lines]
    if len(ids) != len(set(ids)):
        out.append("caption.required_lines ids are not unique")
    for i, line in enumerate(rules.caption.required_lines):
        if not line.options:
            out.append(f"caption.required_lines[{i}] ({line.id}) has no options")
    for i, m in enumerate(rules.caption.required_mentions):
        if not m.text.strip():
            out.append(f"caption.required_mentions[{i}] has empty text")
    if rules.unknowns:
        out.append(
            f"unknowns lists {len(rules.unknowns)} unverified value(s): set each from brief.md "
            "and delete its entry"
        )
    return out


def safety_notes(rules: CampaignRules) -> list[str]:
    """How unknown safety fields will be enforced (QA fails closed)."""
    out = []
    if rules.audio.music_allowed is None:
        out.append("audio.music_allowed is null: music is treated as NOT allowed")
    if rules.audio.ai_voice_allowed is None:
        out.append("audio.ai_voice_allowed is null: AI voice is treated as NOT allowed")
    if rules.source.policy == "unknown":
        out.append("source.policy is unknown: QA fails every clip until it is set")
    return out


def approve(name: str, root: Path = CAMPAIGNS_DIR) -> Campaign:
    """Human action: mark rules reviewed and pin the brief hash. Keeps comments in rules.yaml."""
    d = campaign_dir(name, root)
    rules_path = d / "rules.yaml"
    if not rules_path.exists():
        raise CampaignError(f"{rules_path} not found")
    rules = read_rules(rules_path)  # validates the human's edits
    if rules.name != name:
        raise CampaignError(f"rules.yaml name is {rules.name!r}, expected {name!r}")
    found = problems(rules)
    if found:
        raise CampaignError("cannot approve until fixed: " + "; ".join(found))
    brief = d / rules.brief_file
    if not brief.exists():
        raise CampaignError(f"{brief} not found")

    sha = sha256_file(brief)
    text = rules_path.read_text(encoding="utf-8")
    text, n_status = re.subn(r"(?m)^status:.*$", "status: reviewed", text, count=1)
    text, n_sha = re.subn(r"(?m)^brief_sha256:.*$", f'brief_sha256: "{sha}"', text, count=1)
    if n_status != 1 or n_sha != 1:
        raise CampaignError("rules.yaml needs top-level `status:` and `brief_sha256:` lines")
    CampaignRules.model_validate(yaml.safe_load(text))
    _atomic_write(rules_path, text)
    return load_campaign(name, root)


def checklist_sections(rules: CampaignRules) -> dict[str, list[str]]:
    """Everything a human must check or keep doing, because code cannot enforce it."""
    p = rules.posting
    posting: list[str] = []
    if rules.platforms:
        posting.append("Post only to: " + ", ".join(rules.platforms))
    if p.min_days_live is not None:
        posting.append(f"Keep every post live for at least {p.min_days_live} days")
    if p.max_posts_per_clip is not None:
        posting.append(f"Post the same clip at most {p.max_posts_per_clip} times")
    for label, value in (
        ("the same clip on multiple accounts", p.same_clip_on_multiple_accounts),
        ("the same clip across platforms", p.same_clip_across_platforms),
    ):
        if value == "forbidden":
            posting.append(f"Do NOT post {label}")
        elif value == "allowed":
            posting.append(f"Posting {label} is allowed")
        else:
            posting.append(f"The brief sets no rule on {label}")
    if p.dedicated_page_required is True:
        posting.append("Post from a dedicated page for this campaign")
    elif p.dedicated_page_required is False:
        posting.append("Any page is fine (no dedicated page needed)")
    posting += p.account_notes

    return {
        "Human checks (code cannot control these)": rules.checklist + rules.video.quality_notes,
        "Posting": posting,
        "Other rules (judge each clip by hand)": rules.other_rules,
        "Unverified values (fix in rules.yaml before approving)": rules.unknowns,
    }
