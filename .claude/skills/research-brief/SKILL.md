---
name: research-brief
description: Use when building the weekly research step that writes a trend brief for a campaign using official APIs only.
---
# Research brief

Output: data/research/<campaign>.trend_brief.json (contract in docs/ARCHITECTURE.md).

1. Read the campaign rules and the source transcript. Ask the light model in config/models.yaml
   for 3-5 topic keywords.
2. For each keyword, call the YouTube Data API search endpoint, ordered by view count, recent
   uploads, and read titles and public statistics. Respect quota: cache results in data/research/
   and cap searches per run.
3. Summarise the hook patterns and angles that work, in new wording. Record patterns, never copy
   other people's scripts, footage or distinctive phrasing.
4. Hard limits: no scraping or unattended browser crawling of Instagram, TikTok or any other site.
   No Reddit unless I confirm API approval. Content I paste in manually is fine.
5. Read YOUTUBE_API_KEY from the environment via python-dotenv. Never print it.
