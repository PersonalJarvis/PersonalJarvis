# README interface films

Three nine-second, silent Remotion compositions explain the desktop workspace.
They are deterministic UI recreations with sample data, not screen recordings,
measured response times, or proof of successful agent executions.
This public documentation generator uses synthetic data only; private captures
and internal marketing projects are not inputs.

| Composition | Interface sources | Still |
|---|---|---|
| JarvisOrchestrator | `components/home/VoiceStage.tsx`, `JarvisBar.tsx`, `Greeting.tsx`, `StageWaveform.tsx` | [Voice preview](../../assets/demo/readme-2026-09/jarvis-orchestrator-v2.png) |
| JarvisAgents | `components/society/card/AgentCardOverlay.tsx`, `AgentRoutineDetail.tsx`, `roster/RosterRail.tsx`, `chat/AgentChatPanel.tsx` | [Agents preview](../../assets/demo/readme-2026-09/jarvis-agents-v2.png) |
| UltraSwarm | `views/swarm/UltraSwarmView.tsx`, `components/swarm/`, `i18n/locales/swarm/en.json` | [Swarm preview](../../assets/demo/readme-2026-09/ultra-swarm-v2.png) |

Paths in the middle column are relative to `jarvis/ui/web/frontend/src`.
Each composition fills the frame with its section, with global navigation closed.
The narrow native caption follows the app; the Agents roster and Options pane
remain part of the section. No marketing border or extra title strip is added.
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

`settings.json` owns the frame rate, dimensions, duration, and selected frames.
`preview` renders eight representative frames, including both loop endpoints.
`render` writes 3840 x 2088, 60 fps H.264 masters into `out/`. The aspect ratio
matches the section reference. Frames are captured losslessly as PNG before
video encoding, preserving small text and thin UI lines. Remotion manages
its own headless renderer; no browser extension or desktop control is required.
Fonts and product UI assets resolve locally.

To render one composition:

```bash
npm run render -- --id=JarvisAgents
```

The README exports are 1600-pixel GIFs at 25 fps with a full 256-color palette,
1920 x 1044 MP4s at 60 fps, and still previews. GIF frames use an exact 40 ms
cadence instead of uneven frame delays. All three clips last nine seconds and
return to their initial view at the loop boundary.
With FFmpeg installed, export them from the masters:

```bash
node scripts/export.mjs
```

Export also writes explicitly BT.709-tagged `*-4k.mp4` masters into `out/`.

Still previews provide a non-animated alternative. Inspect representative
frames, transitions, and loop boundaries before replacing the README assets.
This is T1 documentation/media work; it does not modify the runtime UI.
