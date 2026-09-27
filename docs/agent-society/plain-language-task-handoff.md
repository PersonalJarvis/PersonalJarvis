# Plain-language tasks in Agents

## Decision and acceptance

The Agents view now leads with one request box. Posting a sentence creates a
durable Society quest; the existing router picks a fitting active agent or
creates a reusable specialist or generalist. The chat and advanced agent
controls remain available for follow-up and deliberate customization.

Acceptance for this change:

1. A person can start work in Agents without choosing an agent, model, effort,
   tool or workflow. The task retains an owner and visible progress.
2. A missing login, approval or decision is an explicit blocked outcome with
   one concrete action. The user can open the owning agent and retry afterward.
3. A result card links to checked files in the agent workspace and safe web
   links. A chat reference is never presented as a deliverable.
4. A finished model turn without independent proof is labeled `reported`, not
   verified completion. Only an explicitly reported result with an existing
   workspace file is labeled `done` by this path. Partial and blocked reports
   remain retryable. Permission checks and tool execution remain unchanged.
   The visible handoff uses the agent's final answer when present; an earlier
   outcome report cannot replace a more useful final response.
5. The task router accepts the original user text in any language or script.
   A configured capable model suggests only connected capability IDs; trusted
   Python chooses the agent and the scheduler applies its existing gates.
   If the model is unavailable or returns invalid IDs, the legacy lexical
   route and then a generalist or coordinator keep the task moving.
6. The Agents HTTP path returns a durable `open` receipt before model routing.
   A canceled request cannot assign work after routing finishes; waiting tasks
   recover after a process restart. Brain startup waits retry automatically.

## Three one-sentence requests

| Request | Before | After | Automated evidence |
| --- | --- | --- | --- |
| Open a public page and report its heading and purpose. | The automatic task entry lived in Map; one live POST waited through model routing, and an early outcome summary hid the useful final answer. | The Agents box returned an `open` receipt in 0.69 seconds. Runner worked without a manual agent/model/tool choice; the card displayed the actual heading and purpose as a `reported` answer. | One sentence and one submit. The final answer and a successful page fetch were observed in the isolated Dev instance; the answer has no independently checked source citation. |
| Read private profile settings behind a login. | A blocker could be buried in an agent chat, and a stateless shell could not carry a browser login forward. | Runner opened the exact URL in its selected browser, saw the sign-in redirect, and returned `blocked`. The card puts the sign-in step and controls before the long explanation; its sign-in action opens that exact URL in the agent's headed browser profile. | One sentence and one submit; the person must sign in and retry. The test opened and closed the sign-in window without credentials. No private settings were read. |
| Create a short Markdown guide and leave the file in the task workspace. | A chat session ID counted as output, and the task card offered no file. | Runner wrote `verify_ai_answers.md`, the card linked an existing workspace file, and the local opener returned `opened: true`. | One sentence, one submit, one file click; no manual agent/model/tool choice. The file and its contents were inspected. |

## Scope and limits

This is a T3 routing-contract change alongside a Society task and chat change.
Agent-owned private memory, automatic effort, selected browser ownership and
approval enforcement keep their existing contracts. No OS-specific API or new
provider is used. The route tests cover English, German, Spanish and Japanese
requests with a fake semantic provider; they also check exact catalog ID
validation and preserve the non-Latin task text on the agent turn. The fake
provider proves contract handling, not real multilingual classification quality.
An isolated live classification check using the configured provider returned the
mail capability for English, German, Spanish and Japanese prompts. The first
English attempt failed with a transient provider error, then the same prompt
succeeded on retry. The check used generic prompts and did not access a real
inbox or execute the work. The router tries the configured provider's current
model when the selected Agents model is unavailable.
The isolated Windows Dev desktop served the built bundle. Its browser helper
initially failed because the Python 3.11 host was reused with a lock requiring
Python 3.12; after fixing the helper selection, installation and the browser
render probe passed with managed Python 3.12. The browser-backed login blocker
was observed. The one-shot login route had mistakenly launched the live-stream
runner; after correcting the protocol selection, a headed GitLab sign-in window
opened and the isolated browser profile recorded the sign-in page. A real sign-in
and resumed private result were not performed.
The isolated browser could not reach a loopback test server that the host could
reach, so the external sign-in page was used for the blocker check. A bearer
test endpoint also showed that a model may answer about an HTTP 401 response
instead of marking a differently worded task blocked; such a response remains
`reported` with no verified evidence. Native macOS/Linux execution, a fresh
single-key install and real protected-service acceptance remain unverified.
The local lexical fallback remains limited by its vocabulary; without a usable
model it may choose the generalist for a suitable specialist.
