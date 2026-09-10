# Product films that show the work

**Revision note:** The initial implementation did not realize enough of this motion grammar and introduced low-cadence cursor movement by slowing a 30 fps recording. The corrective, dense temporal analysis and native-60-fps implementation contract are in [MOTION-REBUILD.md](MOTION-REBUILD.md). That revision supersedes the first cut's technical implementation described below.

This analysis covers the two reference films supplied for the Personal Jarvis Agent Mode launch. The useful common pattern is a complete piece of work, made understandable through art-directed UI, selective abstraction, musical rhythm, and a visible human decision.

## Sources and evidence

- [Reference A: debugging across the stack](https://www.youtube.com/watch?v=jwztQLH76is), Claude channel, published September 1, 2026. Downloaded media: 90.0 seconds, 24 fps.
- [Reference B: building an ops review in Slack](https://www.youtube.com/watch?v=G3vwVsh9RtU), Claude channel, published September 1, 2026. Downloaded media: approximately 85.5 seconds, 24 fps; YouTube metadata rounds to 86 seconds.

Method: local video extraction; chronological contact sheets at three-second intervals; inspection of representative full-resolution frames; supplemental multimodal video/audio analysis; FFmpeg loudness measurements; librosa tempo estimates. The auto-caption track of A is largely music labels and unreliable fragments. B has no usable captions. Neither is a narrated explainer. Findings below are grounded in visible action; fine-grained easing and animation timings are reconstruction recommendations, not access to original project files.

The rendered videos do **not** establish the authoring software, exact fonts, animation libraries, stock assets, original music titles, or how much of the UI was captured versus rebuilt. Remotion, HyperFrames, After Effects and hybrid pipelines can all produce this treatment. Claims that a particular library was used would be speculation.

## A — Technical investigation as a story

| Approximate time | Visible action | Editorial job |
| --- | --- | --- |
| 00–06 | A vehicle interface exposes a climate problem and error state. | Establish a specific failure before naming the product. |
| 06–09 | A sparse editorial title briefly interrupts the UI. | State the scope of the promise. |
| 09–20 | Terminal, pasted context, typed request, submit. Tight crops follow the useful part of the input. | Make the instruction concrete and relatable. |
| 20–37 | Several investigation branches become distinct moving clusters and trails. | Explain parallel information gathering without forcing the audience to read every log. |
| 37–51 | Source streams become aligned rows and vertical structures; one repeating delay becomes the focal fact. | Convert the analysis into a visually discoverable pattern. |
| 51–66 | Return to terminal evidence and developer input. Search results expose the suspected timer. | Tie the abstract picture back to the actual work. |
| 66–76 | Request for proof, verification and return to the simulator. | Demonstrate that an answer must survive a check. |
| 76–90 | Visible improvement, short closing claim, brand resolve. | Pay off the opening failure before the logo. |

### Design system observed

Very dark neutral ground, muted white text, warm coral brand accents, sparse secondary terminal colors. Monospace belongs to code and observations; serif belongs to editorial claims. This role separation is more important than copying a particular font. The terminal can occupy the frame; extra browser chrome is discarded when it stops helping the story.

The abstract graphics inherit their vocabulary from the task: records, rows, sources, paths and a repeated interval. They are not a generic AI particle background. Density expands during investigation, then collapses around the one decisive result. The viewer feels the system doing a large amount of work without needing to read all of it.

### Motion grammar

Wide context → active input close-up → wider investigation → evidence close-up → original context. This is a camera hierarchy, not a sequence of random zooms. A movement introduces a new question or reveals an answer. Stillness follows once reading matters. Pointer travel ends on a real control, with the visible result occurring after the press.

Reconstruction: render each record as an indexed object; retain its identity when switching layouts; animate the parent camera and object positions on one deterministic timeline. Use spline trails or SVG paths for connections. Reveal the decisive value after the surrounding records establish a pattern. Restore a recognizable UI surface when asking for human judgment.

## B — Messy inputs, human clarification, finished artifact

| Approximate time | Visible action | Editorial job |
| --- | --- | --- |
| 00–06 | A deadline notification arrives over a desktop of files. | Give the work urgency through a familiar situation. |
| 06–09 | One short title on a calm field. | State the benefit without a feature list. |
| 09–24 | Compose a Slack request, attach prior work, browse contributions and reactions. | Show where the task starts and what inputs exist. |
| 24–42 | Message/file fragments leave the ordinary thread and spread into a spatial workspace. | Make gathering and contextual understanding legible. |
| 42–55 | Sources are inspected; slides start to assemble; conflicting information becomes visible. | Show progress and a meaningful complication. |
| 55–65 | The system raises the discrepancy; a human supplies the missing business context. | Make collaboration the reason the result improves. |
| 65–76 | Inputs reconcile, deck finishes, attachment returns to the conversation. | Deliver a concrete artifact in the original work surface. |
| 76–86 | Team response, closing message, brand. | Prove usefulness socially, then close. |

### Design system observed

Warm pale canvas, light cards, dark type, a thin coral/red line that acts as connective tissue. The actual file formats and app marks remain recognizable. A serif title voice is set apart from the sans-serif UI voice. The background grid is subordinate to the documents; it gives spatial continuity without becoming the subject.

Depth comes from scale, overlap, selective focus, and camera movement. The film does not need heavy perspective on every card. Documents remain recognizable when detached from their original surface. Magnification and selection direct the eye toward the particular number or source that matters.

### Motion grammar

Source card → detached card → inspected source → assembled output → attached deliverable. Maintaining object identity makes this transition chain understandable. The moving line gives continuity between different arrangements. The human clarification is allowed to breathe; the animation does not compete with it.

Reconstruction: begin with a faithful chat surface, measure attachment rectangles, then promote those exact visual objects into a separate composition layer. Animate them to a spatial layout while the source fades. Move one camera through the layout. Draw connecting paths by stroke length. Assemble previews only after the relevant source has been inspected. Return the finished attachment to a matching position in the chat.

## What makes both films professional

1. **One task owns the runtime.** A specific problem leads to a specific output. The feature set is revealed through the work.
2. **The product arrives in context.** Neither opening spends ten seconds explaining the company.
3. **The interface is directed.** Prompts, reactions and results are enlarged separately. A full desktop shot is orientation, not the default reading scale.
4. **Abstraction earns its place.** It explains work that would be tedious to watch literally. It returns to evidence before making the payoff claim.
5. **The human changes something.** A clarification or verification request affects the outcome. This is more credible than an uninterrupted magical success sequence.
6. **Information has a sequence.** Input first, then action, then result. The viewer is never required to decode everything simultaneously.
7. **Motion changes with the task.** Typing, investigation, reading and delivery have different rhythms.
8. **The last output answers the first problem.** The logo follows the payoff.

## Timing rules for reconstruction

These are production targets derived from the observed rhythm, not measurements of proprietary keyframes.

| Element | Practical range | Rule |
| --- | --- | --- |
| Cold open | 4–7s | One familiar problem, clear by the first few seconds. |
| Short title | 2–4s | One proposition; no multi-line explanation. |
| Pointer travel | 0.35–0.8s | Decelerate into a measured target; do not wander. |
| Press feedback | 0.10–0.18s | Let the press register before the resulting state replaces it. |
| Targeted camera move | 0.65–1.1s | Move once, then stop on readable information. |
| Small message entrance | 0.2–0.45s | Position and opacity; minimal overshoot. |
| Editorial arrival | 0.45–0.8s | Long-tail settle; typically power3.out. |
| Short finding | 1.5–3s | Let the audience recognize the fact. |
| Multi-line prompt/result | 3–7s | Match hold time to words that must actually be read. |
| Final brand hold | 3–5s | Name and next destination remain stable. |

At 30 fps, a 0.7-second reframe is 21 frames. Do not confuse timeline precision with the need to animate everything. Retain motion-free reading windows. Type text with punctuation-aware pauses; avoid a uniform typewriter delay that sounds and looks mechanical.

## Audio and musical structure

The references use instrumental, rhythm-led beds rather than spoken narration. Supplemental listening analysis identifies a warm, syncopated electronic/funk character with bass, keyboard textures and percussion; the precise instruments and track identities remain unverified. Some apparent UI sounds can also be musical transients, so do not claim a specific sound-effect library.

Measured on downloaded reference audio:

| Measure | A | B |
| --- | --- | --- |
| Integrated loudness | −16.58 LUFS | −19.38 LUFS |
| True peak | −1.15 dBTP | −0.24 dBTP |
| Loudness range | 8.4 LU | 5.5 LU |
| Automated tempo estimate | ~143.6 BPM | ~107.7 BPM |

Tempo estimation can select a subdivision or double/half tempo; these estimates are not certified tempos. A listening model estimated roughly 110 BPM for both, which conflicts with the measured pulse of A. The useful conclusion is rhythmic contrast and a steady groove, not false numerical certainty.

For a new film, select and license a separate instrumental. Align major editorial changes to musical phrases where it helps comprehension; do not force every mouse movement onto a beat. Keep the track continuous across visual cuts. Reserve quiet effects for significant actions. Finish on a clean musical phrase or a deliberate fade, never an accidental cut or silent tail.

The Agent Mode film uses **Cipher**, Kevin MacLeod, from the composer's official catalog: synths, electric piano, percussion and strings; catalog tempo 150 BPM; described as bright, grooving and uplifting. It is a distinct track, not music extracted from either reference. A 12.8-second source offset starts the selected 85-second section; the composition owns the final fade. The upload description contains the required attribution and notes the edit. [Official catalog and licensing metadata](https://incompetech.com/agent-section/), [track page](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100844), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Translation into Personal Jarvis

The chosen story is a launch briefing. It has three concrete subjects: open issues, the release checklist and the welcome email. Jarvis receives the brief. Scout asks Archivist for missing context. The human narrows the email task directly with Scout. Jarvis returns the briefing and an explicit next action.

The real capture imports the product's WorldStage, RosterRail, AgentCardOverlay and conversation stores. Its isolated fixture supplies synthetic English examples and records clicks against actual controls. The film preserves those pixels and the original character/world assets. It does not claim to show a live customer session, real external API execution, or measured completion times.

The branding translation is deliberate: warm charcoal and gold, Inter, the official ghost mark, and the product's own world. No competitor logo, coral signature, serif identity, reference footage, or reference audio is included in the exported film. Abstract processing is represented by the product's own visible agent conversations, rather than inventing an interface the product does not have.

The source footage is 2160×1896. An unchanged full-frame fit would leave critical dialogue too small. The new 1920×1080 composition therefore uses measured camera crops, enlarged message regions and a separate final-result crop. Reading time is expanded. Execution timing is illustrative, and this limitation is stated in the upload copy.

## Reusable production checklist

- Can a viewer explain the problem by the end of the cold open?
- Does every shot advance this particular task?
- Is each essential sentence legible at ordinary YouTube viewing size?
- Does a pointer land on the control whose result follows?
- Are the product states supported by the actual interface?
- Are illustrative data and edited time distinguished from evidence?
- Does an abstract transition preserve the identity of its source material?
- Is the human decision visible and consequential?
- Does the result answer the opening problem?
- Are audio rights, fonts and branding documented?
- Does a seeked preview match the exported pixels at the same time?
- Does the final MP4 have continuous audio, the intended duration, and no blank transition frames?
