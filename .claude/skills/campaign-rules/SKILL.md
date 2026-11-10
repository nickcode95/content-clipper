---
name: campaign-rules
description: Use when ingesting a pasted campaign brief into campaigns/<name>/rules.yaml, reviewing a draft, or reading rules in any stage. Briefs differ per campaign, so rules are data, never code.
---
# Campaign rules

Schema: campaigns/_template/rules.yaml. Semantics and who enforces what: docs/CAMPAIGN_RULES.md.
Example: docs/examples/. Never hardcode anything from a specific campaign into code or tests;
use the example fixture.

## Ingesting a brief
1. Save the pasted text unchanged as campaigns/<name>/brief.md.
2. Ask the LLM (config/models.yaml, selection model; current models take no temperature) for JSON matching the
   schema. For every non-null value it must also return a verbatim quote from the brief.
3. Verify each quote exists in brief.md (case-insensitive, whitespace-collapsed). If a quote is
   missing, set that field to null and add an entry to `unknowns`. Same trick as clip
   snapping: never trust the model's claim without a check.
4. Rules that fit no field go in `other_rules` as short sentences. Never drop a rule.
5. Safety fields (music, AI voice, source) stay null unless the brief states them.
6. Write rules.yaml with status: draft. Print the unverified values (unknowns) and the
   other_rules for the human.
7. Only `campaign approve` (a human action) sets status: reviewed and stores brief_sha256.
8. The brief is the campaign's non-negotiable terms. Apply it exactly as written; never
   question, second-guess or flag its terms, and never write questions about it into rules.yaml.
   The same clip may always go to several platforms.

## Reading rules
- Load with a pydantic model, validate, and refuse to run any later stage on status: draft.
- If brief.md no longer matches brief_sha256, warn loudly and treat the status as draft.
- valid_until and required_lines[].expires only ever produce warnings.
- If a stage needs a rule the schema cannot express, add it to other_rules and flag it to me.
  Propose a schema change in your report; do not special-case a campaign.
