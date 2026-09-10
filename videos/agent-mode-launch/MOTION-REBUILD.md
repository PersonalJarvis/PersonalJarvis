# Motion rebuild — revision 2

The rejected first cut slowed a 30 fps recording to playback rates of 0.336–0.407. This supplies only about 10–12 distinct source images per output second. A 60/120 fps export of that same footage would repeat frames and retain the defect. Revision 2 removes the video layer and renders the original isolated product fixture at every output timestamp. The cursor is an independent vector object whose position is evaluated continuously; fast travel is separated from longer reading holds.

## Reference motion inventory

Dense inspection: six samples per second across short transition windows, rather than the previous three-second overview sampling.

| Reference window | Observed treatment | Reconstruction for this film |
| --- | --- | --- |
| Debugging, 18–22s | Text/terminal plane breaks into small blocks while the camera lifts away into an abstract space. Pixels and source text overlap during the transition. | The actual request recedes into a brief card; its two clauses split into the specialists' task cards. Shared text maintains identity across the handoff. |
| Debugging, roughly 22–37s | Several source clusters develop separately; moving trajectories connect a shared investigation. | Three named agents and two real task branches; SVG connections draw on and packets travel continuously along their paths. |
| Debugging, roughly 37–49s | Dense arrays align, camera travels through them, then isolates one repeated finding. | Checklist rows assemble in stages and concentrate onto the unresolved welcome-email item. No unsupported data metric is invented. |
| Ops review, 37–41s | A circular aperture occludes the surrounding workspace. Its contents change through vertical travel and focus changes; the aperture is larger than the source object. | Circular masked inspection of the checklist, expanding to reveal the complete briefing sheet. |
| Ops review, 45–49s | Camera moves down the work axis; cards/slide sheets enter from different depths and align into an artifact. | Three evidence sheets slide into a single launch briefing, with staged row reveals and a held readable result. |
| Both, interaction shots | Input close-up, purposeful cursor motion, short press feedback, then an immediate change of viewpoint. | Newly rendered vector pointer, measured actual control centers, 0.45–0.75s cubic-eased travel and a 0.14s press. |

These describe observable motion. They do not identify the original authoring application or proprietary easing curves. Source files and the exact original motion implementation are unavailable.

## Additional close inspection

The overview frames hid several important treatments. Additional six-samples-per-second strips cover debugging 30–34s and 42–46s, and the ops-review 23–27s window:

- **Camera push through a data field:** three distinct round clusters settle, then grow into the foreground until individual columns fill the image. This is a change of camera scale and depth, not simply opacity on a static dot background. Source: debugging 30–34s. For reconstruction, preserve the same indexed dots while changing camera projection and source layout; never regenerate random positions on each frame.
- **Vertical reveal with accelerating information density:** a sparse table header gives way to aligned columns, then the camera travels into the records. Labels move out of the reading zone as the records take over. Source: debugging 42–46s. A staged data reveal followed by a camera move explains the hierarchy; revealing every column at the start would destroy the effect.
- **Directional blur during a whip scroll:** the ops-review thread stretches into vertical streaks, then resolves into a different part of the same conversation. The frame is not uniformly blurred throughout the shot. Blur is strongest during fast travel and disappears for reading. Source: ops review 23–27s. This is distinct from a slow zoom or a crossfade.
- **Document identity survives the camera change:** recognizable attachment chips remain coherent before and after the whip. Maintaining their identity is what makes a fast move readable.

## Practical effect recipes

| Family | What actually moves | Reproduction rule | Common failure |
| --- | --- | --- | --- |
| Kinetic typography | Individual masked words/lines | Stagger arrivals, then hold the complete statement; animate from explicit initial positions | Every sentence flies in identically |
| Targeted UI zoom | One scene camera | Compute source-to-screen transform from target bounds; decelerate before reading | Center zoom loses the relevant control |
| Cursor choreography | A vector pointer on its own clock | Curved travel, continuous subpixel positions, short target dwell, press after arrival | Stretching cursor speed to match a long reading hold |
| Whip scroll | Whole content plane plus directional blur | Accelerate, blur at peak velocity, decelerate, resolve completely | Leaving text blurred during the hold |
| Pixel dissolution | The actual source plane split into indexed cells | Preserve a recognizable portion of the source while cells release into the next scene | Decorative particles unrelated to the task |
| Cluster formation | Many objects into a few stable groups | Use deterministic destinations, shared easing and a clear hierarchy | Continuous random motion with no information |
| Traveling connections | Stroke reveal plus a moving packet | Draw the route before/with the packet; attach endpoints to source objects | Detached connector ends or looping dots with no purpose |
| Spatial workspace reveal | Camera scale and translation | Start close to an identifiable item, then expose surrounding context | Scaling a screenshot so far down that all text becomes noise |
| Circular inspection | An aperture and its interior | Keep the aperture coherent while panning or swapping the inspected detail | A circular wipe used only as decoration |
| Selective focus | Focused content sharp, context less prominent | Briefly use depth cues to identify the subject, then restore readability | Full-screen blur disguising a weak layout |
| Document assembly | Independent sheets into one artifact | Stagger depth/position arrivals; align edges before the final content reveal | Three static cards presented as a finished animation |
| Drawn emphasis | A line or loop around a specific fact | Reveal the fact first, then draw the accent once | A scribble over every word |
| Content-to-output morph | A persistent card/attachment | Match position, scale, radius and identity at the handoff | Two unrelated rectangles crossfading |
| Logo resolve | Mark and type in a short sequence | Finish the useful result first; keep the last lockup stable | A long logo intro or an empty gap before the mark |

Revision 2 applies the families that explain this product's real example: kinetic type, measured UI zooms, independent cursor movement, task splitting, traveling connections, circular inspection, drawn emphasis, document assembly, zoom-through and a short logo resolve. It does not copy the debugging film's task-specific particle data or pretend those graphics are product features.

## Smoothness versus playback rate

A 60 fps container is not proof of 60 fps motion. Three properties must be separated:

1. **Temporal sampling:** evaluate new poses at `frame / 60`, rather than duplicating 30 fps frames.
2. **Velocity:** a 0.62-second mouse movement remains a 0.62-second movement even when surrounding text needs more reading time.
3. **Easing:** the path accelerates and decelerates continuously. Small end-of-move displacements are intentional settling, not a multi-frame freeze.

The new cursor is evaluated analytically from a cubic path. Its start and end positions come from measured native controls. Native UI state changes use a separate piecewise narrative clock; reading pauses therefore do not stretch pointer travel. The full composition contains zero video elements. The export is still checked at the pixel level because an analytical test cannot catch capture/encoding faults.

## Editorial and technical contract

- 85 seconds, 1920×1080, native 60 fps (5,100 evaluated frames).
- No slowed footage, frame interpolation or duplicate-frame frame-rate conversion.
- One native app instance retains state and uses synthetic data; no real messages or external jobs.
- Native product clocks and animation mixers are evaluated at the requested timestamp.
- Separate cursor timing from product content timing; retain measured control hit targets.
- Eight visual phases with intermediate reveals and internal transitions; no repeated title-and-static-screen template.
- Camera settles before essential reading; transition energy is concentrated around semantic changes.
- Motion recipes: viewport-change, cursor-click-ripple, card-morph-anchor, center-outward-expansion, dynamic-content-sequencing, zoom-through-transition.
- Keep existing charcoal/gold branding, official mark, soundtrack and use case.

## Acceptance evidence

Render to 60 fps directly; verify 5,100 frames and 85 seconds. During a selected 0.6-second pointer travel, inspect every output frame and require changing pointer positions with no multi-frame freezes. Verify native UI state before/after the real measured Send targets. Inspect all scene midpoints and transitions. Check audio continuity and the final result's readability. Frame-rate metadata alone is insufficient proof of smooth motion.
