# Profile companion reference

The seven existing SVG profiles are the source silhouettes. Browser-sampled
outlines are retained as JSON, authored as bevelled meshes in an editable
Blender scene and exported as one cached GLB. Gigi reuses the existing first-party
master; its profile image is a transparent render of that same character.

The reference uses the actual application renderer and its stored character
recipes. A temporary QA agent was created, configured, saved and reopened through
the real UI/API. Its independent companion appearance survived page reload.
Live movement receipts changed position from [277.578, 58, 70.579] to
[263.540, 58, 61.310] while the character and companion were rendered.
Screenshots establish appearance, not a frame-rate claim.

The shared schema corpus covers Python, JSON storage, TypeScript and the editor.
Headless create/edit/reopen tests require no provider, key, GPU or audio device.
Follower checks cover corners, ground height, following distance, discontinuities,
bounded history and background-tab catch-up. The existing canvas lifecycle hook
owns WebGL context recovery/release; per-agent clones share geometry and dispose
their own materials. Reduced motion removes bobbing while preserving deliberate
following. Physical macOS/Linux checks remain unperformed.

The current Mars foundation is separate uncommitted work in the shared checkout.
`mars-integration.patch` records only this feature's additions to that foundation;
it is applied in the live checkout and must accompany the foundation when it lands.
The tracked legacy island integrates the same follower directly.

This adds the requested companion system; it does not qualify or redesign the
world, buildings, rover or existing character families. Further art-family rollout
still requires its own reference review.
