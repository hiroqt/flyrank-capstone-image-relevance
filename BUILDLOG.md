# BUILDLOG: AI-usage log

Brief §3: "Use AI tools freely, but keep BUILDLOG.md honest: where AI helped, where it was wrong, what you changed."
One entry per working session. Honesty is graded; perfection is not.

---

## 2026-11-25 · Planning, design doc, Phase 2 skeleton

**Where AI helped**
- Claude Code (Opus 5.5) read the capstone brief PDF and drafted `PLAN.md`, a traceability plan that maps every §6 requirement, §13 probe and shared requirement to a build location and an evidence item.
- Claude Code drafted `docs/DESIGN.md`, the Phase 1 one-page design doc.
- Claude Code scaffolded the Phase 2 skeleton: FastAPI app, layered folders, config, Docker Compose, the Alembic initial migration, error handling, tenant dependency, the `ImageMetadata` schema and the first tests.

**Where AI was wrong / what I changed**
- _To fill in after reviewing: list anything you disagreed with, corrected, or rewrote._

**Things I must be able to explain without help**
- _To fill in: e.g. why the guard runs every check instead of stopping at the first failure; why there is a unique index on (tenant_id, sha256)._
