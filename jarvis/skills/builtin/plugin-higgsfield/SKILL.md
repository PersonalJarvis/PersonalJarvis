---
schema_version: "1"
name: plugin-higgsfield
description: Generate images, videos and characters with the user's Higgsfield account.
when_to_use: Use when the user mentions Higgsfield, or asks to generate an image, a video or a character with its models.
category: creativity
plugin_id: higgsfield
intent_verbs: [generier, erstell, mach, generate, create, make, genera, crea]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [higgsfield, higgs field, higgsfield-bild, higgsfield image, higgsfield-video, higgsfield video, higgsfield-character, higgsfield soul]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: "(higgsfield|higgs field)"  # i18n-allow: spoken-input vocabulary
requires_tools: [higgsfield]
risk_policy:
  default_tier: monitor
---

Use the higgsfield/* tools to generate images, videos and characters.

- Pick a model only when the user names one; otherwise let the tools choose and say which one ran.
- State the credit cost and wait for the user's OK before generating. There is no spend cap.
- Images usually finish in seconds. Videos can take several minutes — wait for the polled result rather than retrying.
- If a call fails for credits or plan limits, say so plainly. Unlimited and free generations on the Higgsfield website do not apply here.
- Hand back the result URL. Do not invent a file that was not returned.
