---
name: music-licensing
description: Use when adding or choosing background music for a clip. Enforces the music manifest, licence proof and per-platform eligibility.
---
# Music licensing

Source of truth: config/music_manifest.yaml. Every entry needs file, source, license_url,
platforms, attribution (if required) and moods.

Eligibility function: a track is eligible only if it is in the manifest, has a license_url, its
platforms list includes the target platform, and the campaign rules do not forbid music.
Choose among eligible tracks by mood. If none qualify, use no music. When unsure, no music.

Reminders (verify each licence yourself):
- A licence for one platform does not cover the others. YouTube Audio Library tracks are not
  cleared for TikTok or Instagram.
- TikTok expects commercial accounts to use its Commercial Music Library.
- Free archives can include tracks uploaded by people who do not own the rights. Prefer clear licences.
- Footage may already contain copyrighted music. Flag it for review instead of ignoring it.
- If a track requires attribution, append the attribution text to the caption and log it.
- Keep licence proof (page URL or saved text) in licenses/ for every track.
