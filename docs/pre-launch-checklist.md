# Public-Repo Pre-Launch Checklist (reusable template)

A fail-closed checklist to run **once** before a repository (or a private repo's
history) becomes public for the first time. Work top to bottom; a CRITICAL item
that is not satisfied **blocks the launch**. Tick every box in a fresh clone, not
in your working tree.

> Golden rule: the public surface is the **entire history + all releases**, not
> just the latest commit. A secret that ever existed in any commit stays visible
> forever once the repo is public — deleting the file later does not remove it.

---

## CRITICAL — must be green before the first public push / private→public flip

### Secrets & credentials
- [ ] Scan the **full history** (every commit, every branch), not just HEAD:
      `gitleaks detect --no-banner` and/or `trufflehog git file://. --only-verified`.
- [ ] No real `.env`, `*.pem`, `*.key`, `service-account*.json`, token, or password
      is tracked: `git ls-files | grep -iE '\.env$|\.pem$|\.key$|secret|credential'`.
- [ ] Every key that has **ever** sat in cleartext on a dev machine or in a chat is
      **rotated** before launch (assume compromised, revoke + reissue).
- [ ] Enable **GitHub Secret Scanning + Push Protection** on the repo before flipping.

### Personal / identifying data (PII)
- [ ] No real names, private emails, home/usernames paths (`C:\Users\<name>`,
      `/home/<name>`), phone numbers, internal hostnames in tracked files.
- [ ] **Commit author metadata** is clean — the classic blind spot. File scrubbers
      miss it: `git log --all --format='%an <%ae> / %cn <%ce>' | sort -u`. Use
      `.mailmap` + a noreply email so private addresses never show on GitHub.
- [ ] No internal-only docs, scratch notes, incident reports, or red-team logs leak.

### Licensing & legal
- [ ] A `LICENSE` file exists and matches what the README claims.
- [ ] Third-party code/assets are attributed; bundled fonts/images are
      redistributable; trademarks are acknowledged.

---

## IMPORTANT — fix before launch, not strictly a hard block

### Documentation & first impression
- [ ] `README` — what it is, install, quickstart, screenshots that actually load.
- [ ] `CONTRIBUTING.md`, `SECURITY.md` (real private reporting channel),
      `CODE_OF_CONDUCT.md`, `CHANGELOG.md`.
- [ ] `.env.example` / config-example files cover every required variable, with
      **no real values**.

### CI / supply chain
- [ ] CI workflows have **least-privilege** `permissions:` (default read-only);
      no secrets echoed to logs; no `pull_request_target` that checks out and runs
      untrusted fork code.
- [ ] Dependabot or equivalent is on; lockfiles are committed and current.
- [ ] Branch protection on the default branch (require PR + green checks).

### Build reproducibility
- [ ] Fresh clone builds/installs from documented steps on a clean machine.
- [ ] Pinned dependency versions; no path-dependent or machine-local assumptions.

---

## NICE-TO-HAVE — polish, can follow shortly after launch

- [ ] Issue/PR templates under `.github/`.
- [ ] A tagged release (`v1.0.0`) with release notes, not just a bare push.
- [ ] `.gitattributes` for consistent line endings (esp. Windows projects).
- [ ] Repo description, topics, social-preview image, pinned README sections.
- [ ] Badges (build, license, version) all resolve; community/chat invite links valid.
- [ ] Large binaries reviewed (Git LFS if needed); no accidental dumps.

---

## Verification commands (run in a fresh clone)

```bash
git ls-files | grep -iE '\.env$|\.pem$|\.key$|secret|credential|service-account'
git log --all -p | gitleaks stdin            # or: gitleaks detect
git log --all --format='%an <%ae>' | sort -u # author-metadata leak check
git grep -nIE 'AIza[0-9A-Za-z_-]{30}|sk-[A-Za-z0-9]{20}|ghp_[A-Za-z0-9]{36}'
git ls-files | xargs du -k | sort -rn | head # biggest tracked files
```

A finding in any CRITICAL command = **stop and remediate before going public.**
