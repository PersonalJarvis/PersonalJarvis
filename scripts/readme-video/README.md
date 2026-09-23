# README interface films

Three 15-second, silent Remotion compositions explain the desktop workspace.
They are deterministic UI recreations with sample data, not screen recordings,
measured response times, or proof of successful agent executions.
This public documentation generator uses synthetic data only; private captures
and internal marketing projects are not inputs.

| Composition | Interface sources | Still |
|---|---|---|
| JarvisOrchestrator | `components/home/VoiceStage.tsx`, `JarvisBar.tsx`, `Greeting.tsx`, `StageWaveform.tsx` | [Voice preview](../../assets/demo/readme-2026-09/jarvis-orchestrator.png) |
| JarvisAgents | `components/society/card/AgentCardOverlay.tsx`, `AgentRoutineDetail.tsx`, `roster/RosterRail.tsx`, `chat/AgentChatPanel.tsx` | [Agents preview](../../assets/demo/readme-2026-09/jarvis-agents.png) |
| UltraSwarm | `views/swarm/UltraSwarmView.tsx`, `components/swarm/`, `i18n/locales/swarm/en.json` | [Swarm preview](../../assets/demo/readme-2026-09/ultra-swarm.png) |

Paths in the middle column are relative to `jarvis/ui/web/frontend/src`.
The shell follows `components/layout/Sidebar.tsx` and `TopBar.tsx`.
GigiMark, Badge, Button, and Switch are imported from the application.
Generated CSS uses the application's Tailwind configuration, theme tokens,
and bundled fonts. Network-dependent views have pure visual adapters here;
their markup and state are driven by the video frame.

Voice and Agents were checked against the September 23 source. Ultra Swarm
follows commit `8434ce9b6e107c0b28789c144243e4b2b784723a` in
[PR #186](https://github.com/PersonalJarvis/PersonalJarvis/pull/186). Keep its
development-preview label until the feature reaches the default install.
Re-check this mapping when the product UI changes.

All names, messages, routines, and task assignments are sample data. The
Agents routine has no execution history. Swarm shows assignments and
dependencies without a fabricated accepted result, throughput, or usage.
No microphone, account, API, credential store, or live workspace is used.

## Reproduce

From this directory, with Node.js 20+:

```bash
npm ci
npm run check
npm run preview
npm run render
```

`preview` renders frames 0, 90, 180, 270, 360, and 449 for every composition.
`render` also writes 3840 x 2160 H.264 masters into `out/`. Remotion manages
its own headless renderer; no browser extension or desktop control is required.
Fonts and product UI assets resolve locally.

To render one composition:

```bash
npm run render -- --id=JarvisAgents
```

The README exports are 1000-pixel GIFs, 1600 x 900 MP4s, and still previews.
With FFmpeg installed, export them from the masters:

```bash
node scripts/export.mjs
```

Export also writes explicitly BT.709-tagged `*-4k.mp4` masters into `out/`.

Still previews provide a non-animated alternative. Inspect representative
frames, transitions, and loop boundaries before replacing the README assets.
This is T1 documentation/media work; it does not modify the runtime UI.
