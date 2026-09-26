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
5. The task router accepts the original user text in any language or script.
   A configured capable model suggests only connected capability IDs; trusted
   Python chooses the agent and the scheduler applies its existing gates.
   If the model is unavailable or returns invalid IDs, the legacy lexical
   route and then a generalist or coordinator keep the task moving.

## Three one-sentence requests

| Request | Before | After | Automated evidence |
| --- | --- | --- | --- |
| Research the latest battery news (submitted in German). | The lead chat could choose to delegate; the automatic quest entry lived in Map. A normal turn could yield `done` with no evidence. | The Agents request box routes to the research agent. A bare answer is shown as a response with no verification claim. No person intervention is required to start. | The contract test routes the German request and records a `reported` result with an empty evidence list. The content of the answer is supplied by a fake runner, so its research quality is not verified. |
| Research data behind my login (submitted in German). | A turn could end with an unhelpful completion or a blocker visible only in the agent chat. | The assigned agent can report the exact login step as `blocked`; the task shows it, opens that agent, and offers retry after sign-in. The person must complete the login. | The contract test checks the blocker, owner, and retryable state. It does not perform a real login. |
| Research a topic and write a report file (submitted in German). | A chat session ID counted as output and no file was shown on the task card. | The task links an existing file under the agent workspace. A missing or outside path cannot become checked evidence. No person intervention is required to start; the person can inspect the file. | The contract test creates a real file, checks the result path and evidence, and rejects invalid paths in a unit test. It does not assess the report's contents. |

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
No running desktop API was reachable during this work, so live browser, login,
file preview, macOS/Linux execution and a fresh single-key provider run remain
unverified. The local fallback matcher remains limited by its vocabulary;
without a usable model it may choose the generalist for a suitable specialist.
