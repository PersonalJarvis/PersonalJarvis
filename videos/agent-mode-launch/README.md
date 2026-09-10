# Agent Mode — YouTube launch film

An 85-second, 1920×1080, 30 fps HyperFrames product film with an instrumental soundtrack. English, no narrator. The story follows a launch briefing through a lead agent, agent-to-agent context exchange, direct specialist instruction and a final result.

## Files

- `renders/Personal-Jarvis-Agent-Mode-1080p.mp4`: delivery master.
- `REFERENCE-ANALYSIS.md`: source-backed design analysis, observed reference structure, motion recipes, audio measurements and translation into this brand.
- `STORYBOARD.md`: seven editorial beats.
- `DESIGN.md`: palette, typography and motion direction.
- `YOUTUBE-DESCRIPTION.txt`: proposed title, description and required music attribution.
- `build.mjs`: deterministic authoring source; generates the seven frame files and root timeline.
- `index.html`, `style.css`, `compositions/frames/`: editable HyperFrames project.

## Product fidelity

The visual source is the existing capture at `personaljarvisweb/public/agents-demo/agents-feature-v4-sharp.mp4`. It uses actual product components with isolated fixture data. This film edits and reframes that capture. It does not modify the live application, execute an agent, contact external integrations, or redesign the world. The upload copy identifies the synthetic examples and edited timing. This is not a performance benchmark.

## Rebuild

From this directory, with Node.js 22+ and FFmpeg installed:

```sh
node build.mjs
npm run check
npx --yes hyperframes@0.8.33 preview --background
npx --yes hyperframes@0.8.33 render --quality high --fps 30 --workers 2 --video-frame-format png --output renders/Personal-Jarvis-Agent-Mode-1080p.mp4
```

The local handoff archive includes the media, Inter font and GSAP runtime. Repository history omits large rendered media. Restore `assets/agents-source.mp4` from the existing capture above; retrieve `Cipher2.mp3` from the composer's catalog if rebuilding without the handoff archive. The selected music starts at source time 12.8 seconds, lasts 85 seconds, and is normalized to approximately −17 LUFS before the composition fade. No credentials are required to render the prepared project.

## Rights and sources

Music: Cipher by Kevin MacLeod, ISRC USUAN1100844, [official catalog](https://incompetech.com/agent-section/), [track](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100844), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Include the music credit in `YOUTUBE-DESCRIPTION.txt` when publishing.

Inter: included license in `assets/INTER-LICENSE.txt`. Brand mark and product capture: existing project assets. GSAP: copied from the existing project's 3.14.2 package. Camera geometry is adapted from the installed HyperFrames `ui-focus-zoom` component. No third-party reference-video pixels or audio are included in the film.

## Verification

The final source check samples prompts, both agent messages, the specialist reply, the result and the closing frame. Runtime, layout, contrast and lint must pass. Actual screenshots, not just automated findings, govern camera crops. The export is separately probed for duration, dimensions, frame rate, video/audio streams and decodability. See `VERIFICATION.json` for the final evidence.
