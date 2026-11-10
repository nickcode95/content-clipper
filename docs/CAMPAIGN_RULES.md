# Campaign rules: schema and who enforces what

Briefs differ per campaign and change over time, so NO campaign-specific logic lives in code.
A campaign is two files: campaigns/<name>/brief.md (verbatim paste) and campaigns/<name>/rules.yaml
(structured). Template: campaigns/_template/rules.yaml. A filled, fictional example is in
docs/examples/.

## Life cycle
1. `content-clipper campaign add <name> --brief <file>` stores brief.md and asks the LLM to draft
   rules.yaml. Each value the LLM fills must come with a verbatim quote from the brief (`evidence`).
   Code checks the quote exists in brief.md; if not, the value is reset to null and a question is
   added to `unknowns`.
2. A human reads the draft next to brief.md, answers the unknowns, edits, then runs
   `content-clipper campaign approve <name>` which sets status: reviewed and stores brief_sha256.
3. select, caption, render and qa refuse to run while status is draft.
4. If brief.md changes later, the hash no longer matches: QA warns and status goes back to draft.
5. Rules that fit no field go to `other_rules` (never dropped). Do not hardcode them. If the same
   kind of rule keeps appearing, propose a schema change instead.

## Who reads which field

| Field | Select | Caption | Render | QA (code) | Human / LLM review |
|---|---|---|---|---|---|
| source.* | skips unregistered sources | | | blocks if source not in registry.csv | |
| video.min/max_length_s | window limits | | | ffprobe length | |
| video.language | | writes in it | | transcript language | |
| audio.* | | | music on only if allowed AND eligible | render.json shows no music when disallowed | |
| caption.required_mentions | | must include | | string check per platform | |
| caption.disclosure | | builds the tag line | | position and first-hashtag checks | |
| caption.required_lines | | includes verbatim | on-screen lines drawn | string check; expiry warns | |
| caption.max_*, forbidden_phrases | | respects | | checks | |
| allowed_facts | | only source of non-transcript facts | | | LLM checks claims |
| on_screen_text, hook_style | scoring emphasis | writes overlay text | draws overlay | policy check | LLM checks match to screen |
| prohibited, other_rules | rejects violating clips | avoids | | | LLM review, then human |
| posting.* | | | | counts rows in results.csv | |
| checklist | | | | listed by `checklist` command | human |
| valid_until | | | | warning only | |

null in a safety field (music, AI voice, source) is treated as not allowed.
