-- Personal Jarvis pre-marketing readiness audit evidence materialization.
-- Snapshot timestamp: 2026-07-12T11:15:00+02:00.
--
-- Material sources reviewed before these rows were frozen:
--   README.md
--   .github/workflows/ci.yml
--   .github/workflows/fresh-install-smoke.yml
--   docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md
--   docs/plans/cross-platform-mac-linux/LIVE-SIGNOFF-CHECKLIST.md
--   docs/qa/voice-qa-checklist.md
--   local Pytest, Vitest, Vite, Ruff, mypy, pip-audit, and npm-audit output
--   GitHub repository, release, workflow, and branch-protection API results
--   unauthenticated HTTPS responses from https://personaljarvis.ai/
--
-- This SQLite-compatible script materializes the quantitative rows used by the
-- report cards and chart. Qualitative tables and their complete text are frozen
-- in the sibling canonical artifact JSON.

CREATE TABLE headline_metrics (
    metric TEXT PRIMARY KEY,
    value INTEGER NOT NULL,
    target INTEGER NOT NULL
);

INSERT INTO headline_metrics (metric, value, target) VALUES
    ('overall', 48, 85),
    ('backend_failures', 16, 1),
    ('mac_live', 0, 7),
    ('install_endpoints', 0, 2);

CREATE TABLE readiness_dimensions (
    dimension TEXT PRIMARY KEY,
    short_dimension TEXT NOT NULL,
    score INTEGER NOT NULL,
    weight INTEGER NOT NULL,
    evidence TEXT NOT NULL
);

INSERT INTO readiness_dimensions (dimension, short_dimension, score, weight, evidence) VALUES
    ('Website and distribution', 'Website', 20, 10, 'Blank noindex homepage; both stable installer endpoints returned 404; complete local site not deployed.'),
    ('Cross-platform evidence', 'Platform', 30, 15, 'Linux headless evidence exists; zero macOS/Linux desktop live sign-offs; no manual macOS CI run.'),
    ('Operations and support', 'Operations', 38, 5, 'Feedback paths exist, but no launch SLA, beta funnel, incident owner, or verified rollback drill was evidenced.'),
    ('Automated QA', 'QA', 48, 15, '13,470 backend tests pass, but 16 fail and one collection error remains; static checks are report-only.'),
    ('Installation and release', 'Release', 50, 15, 'Signed v1.0.5 and Linux smoke are strong; current code lacks an exact RC and stable website routes are broken.'),
    ('Core product behavior', 'Product', 55, 20, 'Large surface and coverage, but failures hit WebServer, mission routing, Critic fallback, and Wiki completion.'),
    ('UX and onboarding', 'UX', 62, 10, 'Frontend build and 685 tests pass; one Wiki test fails and the manual voice checklist is not signed off.'),
    ('Security and privacy', 'Security', 76, 10, 'Strong private-key, lockfile, dependency, signing, and privacy-gate foundations; branch protection and docs hits remain.');

CREATE VIEW weighted_readiness AS
SELECT
    ROUND(SUM(score * weight) / 100.0, 0) AS overall_score,
    SUM(weight) AS total_weight
FROM readiness_dimensions;

CREATE TABLE verification_summary (
    check_name TEXT PRIMARY KEY,
    passed INTEGER,
    failed INTEGER,
    errors INTEGER,
    status TEXT NOT NULL
);

INSERT INTO verification_summary (check_name, passed, failed, errors, status) VALUES
    ('Backend CI-profile Pytest', 13470, 16, 1, 'FAIL'),
    ('Desktop frontend Vitest', 685, 1, 0, 'FAIL'),
    ('Import cleanliness', 799, 0, 0, 'PASS'),
    ('Marketing-site Astro diagnostics', 39, 0, 0, 'PASS'),
    ('Stable website installer endpoints', 0, 2, 0, 'FAIL');

CREATE TABLE evidence_register (
    evidence_id TEXT PRIMARY KEY,
    observed_value TEXT NOT NULL,
    source_identity TEXT NOT NULL
);

INSERT INTO evidence_register (evidence_id, observed_value, source_identity) VALUES
    ('backend_ci_profile', '13,470 passed; 16 failed; 1 collection error; 12m13s', 'Local Pytest run using the ci.yml marker profile'),
    ('frontend_vitest', '685 passed; 1 failed', 'Local jarvis/ui/web/frontend Vitest run'),
    ('frontend_build', 'Passed with a roughly 2.0 MB main chunk warning', 'Local jarvis/ui/web/frontend Vite production build'),
    ('macos_live_signoff', '0 of 7 macOS rows live-verified', 'docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md'),
    ('website_homepage', 'HTTP 200 blank noindex/nofollow document', 'https://personaljarvis.ai/'),
    ('website_install_sh', 'HTTP 404', 'https://personaljarvis.ai/install.sh'),
    ('website_install_ps1', 'HTTP 404', 'https://personaljarvis.ai/install.ps1'),
    ('public_release', 'v1.0.5 published with signed assets and provenance', 'https://github.com/PersonalJarvis/PersonalJarvis/releases/tag/v1.0.5'),
    ('public_branch_protection', 'Not protected', 'GitHub branches/main/protection API'),
    ('python_dependency_audit', '0 known vulnerabilities', 'pip-audit against requirements.txt'),
    ('app_frontend_dependency_audit', '0 production vulnerabilities', 'npm audit --omit=dev'),
    ('website_dependency_audit', '2 low-severity findings', 'npm audit --omit=dev'),
    ('ruff', '861 findings', 'ruff check jarvis'),
    ('mypy', '750 errors in 180 files', 'mypy jarvis'),
    ('docs_privacy', '20 local documentation hits', 'scripts/ci/docs_privacy_scan.py');
