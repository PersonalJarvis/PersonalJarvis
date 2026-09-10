# Agent Mode — native 60 fps motion edition

An 85-second, 1920×1080 HyperFrames film. Revision 2 replaces the rejected slowed recording with a live, isolated product fixture, a separately choreographed vector cursor and a full editorial motion treatment. Every frame is evaluated directly at 60 fps. English, music-led, no narrator.

## Deliverables

- `renders/Personal-Jarvis-Agent-Mode-60fps.mp4`: revised delivery master.
- `MOTION-REBUILD.md`: dense reference-motion analysis, root cause of the old stutter and revision acceptance criteria.
- `REFERENCE-ANALYSIS.md`: wider analysis of the two references; its first-cut implementation notes are explicitly superseded.
- `STORYBOARD-V2.md`: eight visual phases and their motion treatments.
- `YOUTUBE-DESCRIPTION.txt`: upload copy and music credit.
- `VERIFICATION-V2.json`: export and motion evidence.

## Editable source

`motion-v2.html.in`, `motion.css` and `motion.js` are the authored scene, styling and choreography. `node build.mjs` inserts the motion script into `index.html`. A single root timeline preserves the same product instance and measured camera/cursor coordinates across editorial sections. Older frame files remain historical source and are not mounted by the new film.

The native app bundle is staged into `app/` from `personaljarvisweb/video/agents-hyperframes/app/`. It uses the actual WorldStage, RosterRail, AgentCardOverlay and conversation stores with synthetic data and stubbed networking. It never executes a real agent or sends real messages. The copied bundle is frozen for reproducibility and included in the local source archive; large generated bundles are omitted from Git.

The native fixture records actual control bounds and verifies its own Send handlers before declaring readiness. The film uses those coordinates for its own 0.62-second cubic cursor moves, independently of the narrative clock. Important text bounds determine camera framing. There are no `<video>` elements in the revised composition and no frame-rate conversion of old footage.

## Render

With Node.js 22+ and FFmpeg, run from this directory:

```sh
node build.mjs
npm run check
npx --yes hyperframes@0.8.33 preview --background
npx --yes hyperframes@0.8.33 render --quality high --fps 60 --workers 2 --output renders/Personal-Jarvis-Agent-Mode-60fps.mp4
```

The local source archive includes `app/`, the font, GSAP, music and SFX. No credentials are needed to render the prepared project. The legacy `build-v1` reference and original recording are not required by revision 2.

## Motion treatment

Masked kinetic type; a directional reveal into the real composer; task-card splitting; curved connection draws with traveling signals; staggered task rows; a camera push through the finding; native world focus changes; direct specialist interaction; a three-layer evidence assembly; a circular inspection mask and drawn emphasis; a converging briefing sheet; a short layered brand close. These are editorial explanations of the example workflow, not extra product features.

## Verification

HyperFrames checks runtime, layout, contrast and structure. Native interaction tests check 36 separate pointer poses across 0.6 seconds and verify lead/specialist selection at the important timestamps. Export verification checks 60 fps, 5,100 frames, 85 seconds, complete decode, audio continuity, and actual encoded frame variation during pointer travel. Representative exported frames are visually inspected. Metadata alone is not accepted as motion proof.

## Rights

Music: **Cipher**, Kevin MacLeod, ISRC USUAN1100844; [official catalog and license metadata](https://incompetech.com/agent-section/), [track](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100844), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Include the provided attribution when uploading. The track is excerpted, level-adjusted and faded.

Quiet clicks and transition sweeps use the installed HyperFrames SFX library; its Pixabay license attribution is retained in `assets/SFX-CREDITS.md`. Inter's license is in `assets/INTER-LICENSE.txt`. GSAP 3.14.2, the ghost mark and the original product bundle come from existing project assets. No reference-video footage, audio or competitor branding appears in the film.
