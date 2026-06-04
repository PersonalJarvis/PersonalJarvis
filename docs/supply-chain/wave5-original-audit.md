# Wave 5 — Original audit (verbatim transcript)

> **Provenance:** independent skeptical audit of the Personal Jarvis
> supply-chain hardening at tag `v0.5.0-supplychain-wave4` (HEAD
> `6e2de078…`). Issued to the maintainer on 2026-05-27. Recorded here
> verbatim — punctuation, line-breaks, and casing preserved exactly so
> a future reviewer can compare what the audit said against what
> Wave 5 actually shipped.
>
> **Companion documents:**
> - `docs/supply-chain/wave5-audit-fixes-validation.md` — the post-fix
>   validation report (acceptance gates, red-team scenarios, predicted
>   re-audit verdict).
> - `install/TRUST_ROOT.md` §10 — the in-source trust-root narrative
>   for the four audit fixes.

---

## Audit findings (verbatim)

### Finding 1 — Tag-binding gap → downgrade-replay attack possible

> "Cross-release downgrade replay is not really defended against … If a
> future release patches install.sh, an attacker serving the OLD
> valid-signed bytes at a fresh URL would pass all 4 axes — the
> verifier's freshness gate (Rekor integratedTime ≤ 24 h) is the only
> barrier, and it ages out within a day. **Tag is not part of what's
> signed**." (technically the Fulcio cert SAN does carry the tag in
> `@refs/tags/<tag>`, but the verifier does not currently cross-check
> the SAN's tag against the resolved `$TAG`.)

**Auditor's prescribed fix:** In `install/install-verify.sh` and
`install/install-verify.ps1`, modify the existing stage that decodes
the Fulcio cert SAN. Extract the `@refs/tags/<tag>` suffix from the SAN
URI. Compare byte-for-byte against the current `$TAG` env var (the
user-requested install tag). If they diverge, FAIL-CLOSED with a clear
message: `axis A: SAN tag <X> does not match requested tag <Y> —
refusing (possible downgrade replay)`.

### Finding 2 — Cloned `main` is unsigned (THE big one)

> "The actual application payload is **not** signed. `install.sh` does
> `git clone --depth 1 --branch main` and `installer.py` does zero
> signature checks on the cloned tree. The four-axis chain ends at the
> bootstrap; whatever is on `main` at install time runs unverified."

**Auditor's prescribed fix:** Bind the cloned tree to a specific commit
SHA that's part of the signed release.

- During release-build, capture the current `git rev-parse HEAD` of the
  release-commit-being-tagged. Emit it as `payload-commit.txt` in the
  release assets, signed alongside other artifacts.
- In `install/install.sh` (the stage-2 script), after the `git clone`
  step, run `git -C <clone-path> checkout <expected-sha>` where
  `<expected-sha>` is read from the verified-and-checksummed
  `payload-commit.txt`.
- The verifier already validated `payload-commit.txt` because it's in
  the released artifact set — bind by SHA-256 against the signed
  `checksums.txt`.
- Document this binding in `install/TRUST_ROOT.md` as the FIFTH trust
  axis (Axis E — payload-commit pin).
- This closes the gap: an attacker who flips `main` after release
  can't influence what gets installed, because the install pins to
  the SHA that existed at sign time.

### Finding 3 — in-toto layout overstated

> "`layout.template.json` has NO `signatures` field; it's an *unsigned*
> template. The real authenticity comes from `install-verify.sh` (which
> IS signed) byte-comparing the template's `identity_regexp` against a
> hard-coded constant. Defensible, but it's not in-toto-as-spec'd."

**Auditor's prescribed fix:** Two options — pick the one that's
actually best, not just easiest.

- **Option A (do it right):** Generate a real signed in-toto layout
  via `in-toto-keygen` + `in-toto-record`. The offline-ceremony key
  signs the layout. Verifier checks the signature, not just the bytes.
  This is the in-toto-spec-compliant path.
- **Option B (be honest):** Rename `layout.template.json` to
  `layout-content-anchor.json`. Update docs/TRUST_ROOT.md/threat-model
  to remove the "in-toto layout pinning" phrasing; replace with
  "content-anchored layout assertion baked into the signed verifier".
  Strip any marketing that overclaims.

> If you can do Option A in scope, do it. If it requires architectural
> changes (e.g., the offline key isn't accessible during release-time
> verification), do Option B but make it COMPLETELY honest — no soft
> language.

### Finding 4 — Repo hygiene

> "secret_scanning + dependabot are `disabled` on the main repo …
> signing actor is a personal account, not a protected bot identity."

**Auditor's prescribed fix:**

- `gh api -X PATCH /repos/personal-jarvis/PersonalJarvis -F
  security_and_analysis.secret_scanning.status=enabled -F
  security_and_analysis.secret_scanning_push_protection.status=enabled`
  — enable secret scanning + push protection.
- Add `.github/dependabot.yml` with weekly updates for github-actions
  ecosystem + pip ecosystem.
- Set branch protection on `main`: require status checks
  (sign-installer.yml + cross-runner-hash.yml +
  verify-installer-smoke.yml), require signed commits, disallow
  force-push, require linear history. Use `gh api -X PUT
  /repos/personal-jarvis/PersonalJarvis/branches/main/protection -f
  required_status_checks.strict=true -f
  required_status_checks.contexts[]=...` etc. If any field fails
  because of plan restrictions, document the failure honestly and skip
  just that field.
- Bot-identity migration is OUT OF SCOPE for Wave 5 (requires separate
  GH account setup). Document as Wave 6 candidate.

---

## Acceptance gates (from the audit)

- `install-verify.sh --dry-run` against `v0.5.1` succeeds.
- `install-verify.sh --dry-run` against `v0.5.0` succeeds when
  `$TAG=v0.5.0` (backward-compat).
- `install-verify.sh --dry-run` with mismatched tag (e.g. fetch v0.5.0
  assets but tell verifier `$TAG=v0.4.0`) — must FAIL-CLOSED at the
  new tag-binding stage with the documented error message.
- A new red-team scenario R-Wave5-A: tamper with `payload-commit.txt`
  post-release; `install.sh` must refuse to install.
- `gh repo view personal-jarvis/PersonalJarvis --json
  securityAndAnalysis` shows `secret_scanning.status=enabled`.
- `gh api /repos/personal-jarvis/PersonalJarvis/branches/main/protection`
  returns a populated object.
