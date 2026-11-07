# Handoff — Family wallboard (2026-11-07)

## Where things are

- **Branch:** `wallboard-design` (from `main`), in worktree
  `.claude/worktrees/local-https-guide/`. No git remote configured.
- **Design (approved by owner):** `docs/superpowers/specs/2026-11-07-family-wallboard-design.md`
- **Mockups (approved direction):** `docs/superpowers/specs/mockups/portrait-v1.html`
  (Today, Week, Home) and `mockups/month-v2.html` (Month). HTML fragments made for the
  brainstorm companion; open in a browser for reference.
- **Implementation plan:** `docs/superpowers/plans/2026-11-07-family-wallboard.md`
  (20 tasks, 7 milestones). **Not yet reviewed/approved by the owner.**
- **Unrelated work on another branch:** `worktree-local-https-guide` holds commit
  `411580a` (local HTTPS guide for the old DAKboard widget), not merged into `main`.

## Status

- Brainstorm done, design approved, plan written and committed.
- **No code written.** `wallboard/` does not exist yet.
- Waiting on the owner: (1) confirm the plan covers what they want, (2) choose
  execution: **subagent-driven** (recommended) or **native**.

## Next step

Once the owner confirms the plan, run it with
`superpowers:subagent-driven-development` (or `superpowers:executing-plans` for native),
starting at Task 1. Create a fresh worktree/branch for the implementation from
`wallboard-design`.

## Owner decisions (summary — full list in the design §6)

- Raspberry Pi 4, **touchscreen**, monitor rotated 90° left (portrait); Pi runs only this app; no Docker.
- Pages: Today (style "C · Family planner"), Next 7 days, Month (text in every day), Home (HA); rotate automatically + tap/swipe; easy to add pages.
- Google Calendar via login; **each family member has their own personal Gmail** → one refresh token per member.
- Photos **uploaded through the app, stored on the Pi**, owner sets the order (albums, drag reorder; shuffle option). NAS dropped.
- HA: tap to toggle lights, start/return vacuum, scenes. **Gas boiler out of scope**, no confirm dialogs.
- Settings in a browser page (admin password). Czech UI, switchable to English.

## Gotchas already identified

- Google OAuth client must be set to **"In production"** or refresh tokens expire after 7 days (personal Gmail, External app).
- OAuth redirect is `http://localhost:8080/api/google/callback`; signing in from a phone uses the "paste the address" fallback.
- Pi 4 GPU: no CSS `backdrop-filter` / `filter: blur` on the display; blurred photo backgrounds are pre-rendered on upload.
- Monitor resolution unknown → design for 1080×1920, all sizes relative.
