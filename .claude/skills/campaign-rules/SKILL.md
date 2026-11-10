---
name: campaign-rules
description: Use when turning a pasted campaign brief into campaigns/<name>/rules.yaml, or when checking clips against campaign rules in QA or selection.
---
# Campaign rules

1. Copy campaigns/_template to campaigns/<name>/ and fill rules.yaml from the text I paste.
2. Record only what the campaign page states. Use `unknown` for anything missing and list the
   unknowns back to me as questions. Never invent a rule.
3. The select stage reads min_length_s, max_length_s, required_mentions and prohibited.
   The QA stage re-checks them on the rendered file (length via ffprobe, mentions in the caption text).
4. Flag any rule about AI-generated content, music, or source footage as high priority.
5. Clips come only from footage the campaign authorizes. If source_notes is empty, ask me where
   the footage is allowed to come from before rendering anything.
