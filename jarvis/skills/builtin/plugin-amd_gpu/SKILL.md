---
schema_version: "1"
name: plugin-amd_gpu
description: Read AMD GPU utilization, temperature and driver status.
when_to_use: Use for explicit AMD hardware status requests.
category: hardware
plugin_id: amd_gpu
intent_verbs: [read, show, inspect, check, zeig, lies, muestra] # i18n-allow
intent_objects: [amd, radeon, rocm]
requires_tools: [amd_gpu]
risk_policy:
  default_tier: monitor
---

Use `amd_gpu/read_status` for read-only device data. Report unsupported metrics as unavailable, never zero. If AMD SMI or supported hardware is absent, explain the capability limitation. Do not install or change drivers, clocks or power settings.
