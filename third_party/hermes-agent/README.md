# hermes-agent (adapted portions)

Upstream: <https://github.com/NousResearch/hermes-agent>, pinned at commit
`e473f5a`. License: MIT, Copyright (c) 2025 Nous Research — the full text is
in [`LICENSE`](LICENSE) next to this file.

Personal Jarvis adapts a few well-separated parts of that project for its
agents (prompt texts, small data structures, guard patterns). Every adapted
block carries a header comment naming the upstream file, the commit and the
license. No upstream file is vendored unchanged, and the agent runtime itself
is not taken from upstream.

| Jarvis file | Upstream source |
| --- | --- |
| `jarvis/society/conversation.py` (`SUMMARY_SYSTEM`) | `agent/context_compressor.py` (structured summary sections) |
