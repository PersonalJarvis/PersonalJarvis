# Browser verification review

This review investigates repeated human-verification requests in the visible
Jarvis browser. It covers the managed worker, normal-Chrome handoff, public
login API, profile identity, native input, shutdown, and browser-library prompts.

## Conclusion and limits

Jarvis had genuine defects that could leave a person browsing through the
automation connection despite taking manual control. These are application
defects; they do not reveal a website's private challenge score. The review
does not establish that all reported challenges share one cause or that the
changes eliminate CAPTCHA requests.

There was no running Jarvis-owned browser during this review. No website
challenge, authenticated account, cookie database, or password store was
inspected. Earlier process inspection had confirmed an automated managed
browser, rather than an activated regular-Chrome session, for the reported
Google rejection. That is historical evidence, not the current running mode.

## Findings

| Finding | Evidence and correction |
| --- | --- |
| Manual ownership did not mean ordinary Chrome | `useBrowserView.ts` sent a normal takeover for first-click input and the Take control button. The worker kept Playwright attached. Capable managed sessions now request the disconnected Chrome handoff for these explicit human interactions. Mere viewing still leaves agent tasks alone. |
| Public login calls used the old handoff contract | `session.py` omitted the login flags and sent navigation without the replacement generation. Login completion also omitted the explicit handback flag. The API now uses the successful takeover response's generation and matching entry/exit flags. |
| API ownership could strand the visible user | A synthetic login owner prevented the actual WebSocket viewer from claiming control. API preparation now releases only its temporary lease, without resuming automation. Completion is bound to the exact prepared session and refuses an active viewer owner. |
| A paused transition could claim normal Chrome was ready | `login_mode` is set before selection and shutdown can fail. Separate `login_ready` now requires the actual plain-Chrome native window and absence of automation handles. Failed transitions no longer present old automated pixels as a normal browser. |
| The upstream prompt assumed a cloud CAPTCHA solver | The installed Browser-Use prompt said challenges would be solved automatically. Jarvis uses a local browser. Its explicit instructions now stop and ask for a person instead of assuming this cloud service exists. These instructions are not an automatic challenge detector. |
| Shutdown deadlines could preempt profile flushing | The plain browser's graceful exit allowance exceeded outer browser/Society shutdown deadlines. The browser aggregate now allows 17 seconds, Society 22, the legacy desktop wait 23, and the hard-exit watchdog at least 45. These are ceilings, not delays for a completed close. A delayed fake worker successfully flushed through the actual server/runtime chain; stalled-child cleanup remains bounded. |

## Persistence, network and compatibility

The audited managed profile paths are stable. No automatic cookie clearing,
profile deletion or cookie export was found in the production live path.
Profile selection pins installed Chrome and rejects version downgrades. An
agent's separate profile is not the same cookie/session container as the
person's pre-existing everyday Chrome profile.

The managed automation context deliberately intercepts requests and blocks
service workers for its network boundary. Playwright documents that request
routing disables the HTTP cache and that service-worker requests require
special treatment. The disconnected manual browser does not use this
interception. These are confirmed configuration differences, not proof that
they caused a particular challenge. [Playwright routing documentation](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route)

Read-only metadata found no configured user-system proxy, proxy environment
variables, or connected Windows user VPN. This does not exclude third-party
VPN software, a shared network, or IP reputation. No network settings changed.

Cloudflare documents unsupported production challenge solving through browser
automation frameworks, and identifies browser configuration, connectivity and
detection errors as possible causes of challenge loops. Google likewise
documents unusual automated traffic as a source of reCAPTCHA requests.
[Cloudflare browser support](https://developers.cloudflare.com/cloudflare-challenges/reference/supported-browsers/),
[Cloudflare challenge troubleshooting](https://developers.cloudflare.com/cloudflare-challenges/troubleshooting/challenge-solve-issues/),
[Google traffic guidance](https://support.google.com/websearch/answer/86640)

## Open acceptance and input limitation

Validation for this change: 145 focused backend checks passed in the isolated
feature checkout, 54 frontend checks passed, and both production builds passed.
Desktop integration passed 159 checks with one failure in the unchanged
`test_tool_gates` approval fixture. The same assertion failed against the saved
pre-change browser modules under the same interpreter and desktop permission
code. It is recorded separately; this change does not alter permission policy
or suppress the test. The server's existing import-order lint finding was also
reproduced on the unchanged base.

The integrated desktop checkout already has a separate asynchronous shutdown
drain. Integration preserves that implementation instead of replacing it with
the feature branch's legacy desktop wait. Its force-exit watchdog receives the
same minimum allowance.

Manual click, key, text and scroll forwarding are covered by focused tests.
Held-button dragging is not implemented in the embedded preview. This limits
ordinary sliders and press-and-hold controls; it is not evidence of why a
provider issued an image-grid challenge. No automated challenge solver,
fingerprint spoofing, proxy rotation or browser-security bypass was added.

Real acceptance still needs a person to use the reported websites in the
visible browser, complete any required verification, and confirm that the same
profile retains its session after browser/app restart and explicit agent
handoff. Fakes prove routing, state and lifecycle contracts, not provider
acceptance. No hidden browser was used as a substitute for those checks.


## Session restoration after manual control

Explicit handback requests Chrome's previous session when automation reopens the
owned profile, so open tabs can return without page inspection during sign-in.
The native contract observes fixture HTTP traffic while the browser is disconnected,
then checks tab recovery after explicit handback. Unit checks alone do not qualify
the native transition or real provider authentication.
