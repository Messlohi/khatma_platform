# Active Context

## Current System State

The Khatma Platform is operational and running. The system handles both Telegram bot interactions and web-based access for Quran Khatma management.

## Active Development Areas

### 1. Developer Dashboard

The developer dashboard is being used for:

- Monitoring all Khatmas globally
- Admin-level user management
- System statistics

### 2. Multi-Tenant Architecture

The system supports multiple independent Khatmas:

- Each Khatma has unique ID
- Users are scoped to specific Khatmas
- Admin privileges per Khatma

### 3. Known Active Components

**Database**:

- SQLite with WAL mode enabled
- Auto-migration on startup
- Race condition handling via unique indexes

**Authentication**:

- Telegram-based (user IDs)
- Web-based (PIN system)

**API**:

- RESTful endpoints under `/api/`
- Developer endpoints under `/api/dev/` (secured)

## Recent Changes

- Arabic text normalization implemented
- Multi-tenant Khatma support added
- Developer dashboard enhanced
- Fixed activity log timestamp mapping key (`time` to `timestamp` in JS / API) and resolved `NULL` timestamp issues in database batch operations
- Added auto-assignment feature prompting users with no active assignments to easily claim new Hizbs sequentially or randomly based on selection type
- **Fixed cross-khatma session bleed bug**: All localStorage session keys are now scoped per-khatma using a `sessionKey(base)` helper that appends the `khatmaId`. This prevents a user logged into Khatma A from appearing (incorrectly) logged in on Khatma B. Logout now only removes the current khatma's session keys (not all localStorage), preserving dark mode and language preferences.

### Daily Rotation Khatmas (latest)

- `khatmas.mode` is `open` (legacy free booking) or `rotation`. Rotation seats come from `khatma_members.slot` and round `n` shifts every seat by `rotation_step`, so a 30-person / 2-hizb / step-2 setup covers all 60 hizbs per day and each reader finishes all 60 in 30 days.
- New tables: `khatma_members` (per-khatma seat, quota, active flag) and `khatma_rounds` (closed rounds, closer, auto flag). Rules live on `khatmas`: `hizbs_per_person`, `rotation_step`, `rotation_days`, `rotation_start_date`, `eod_time`, `auto_complete_eod`, `require_eod_confirm`, `current_round`, `rotation_finished`.
- EOD closes lazily on admin reads, through “إغلاق اليوم”, and via `POST /api/cron/eod` (guarded by `DEV_ACCESS_KEY`). `require_eod_confirm` blocks automatic closing; `auto_complete_eod` marks still-pending hizbs as done when a round closes.
- Bulk participant entry: `parse_participants()` accepts newlines/commas/Arabic separators and an optional quota per name (`name: N`, `name = N`, `name - N`). Used both when creating a khatma (`participants` field) and by `POST /api/admin/members/add`.
- Per-member quotas are editable in the admin assignment board (`adminSetMemberPer` → `per_person`). Mid-round changes call `rebalance_round()`, which keeps completed hizbs, never steals another member's hizb, and tops up from free hizbs.
- Admin is warned in the board when hizbs are still unassigned (`unassigned_hizbs`), when members cannot reach their quota (`shortfall`), and when a member's quota is smaller than `rotation_step` (`quota_warnings`). `POST /api/admin/assignment/fill` distributes leftover hizbs up to each quota.
- Backend coverage: `smoke.py`, `open_smoke.py`, `api_smoke.py`, `bulk_smoke.py`, `rotation_test.py` (30 rounds × 60 unique hizbs, auto EOD catch-up, confirm-mode blocking), all passing.

### Configurable Rotation Period (latest)

- `khatmas.rotation_interval_days` (default 1) sets how many days each round lasts. `rotation_days` is the number of rounds; total programme length is `rotation_days * rotation_interval_days`.
- Round `n` covers days `(n-1)*interval + 1 … n*interval` and closes at `rotation_start_date + (n*interval - 1)` days at `eod_time`. Hizbs shift only when a round closes; day 1 layout follows seat order.
- `_round_end()` is the single source of truth for round end times. `process_due_rounds()` computes `due = (elapsed + 1 day) // (interval days)` and closes catch-up rounds. `get_eod_status()` returns `rotation_interval_days`, `total_days`, `next_eod`, and `overdue`.
- Admin UI: creation form and rules tab expose “مدة الدورة (أيام)”; the status card shows the interval, total duration, and that hizbs change at round close. “إغلاق الدورة” honours the stored `auto_complete_eod` (no longer forces completion).
- Manual close no longer forces `complete_pending: true`; when `auto_complete_eod = 0`, pending hizbs stay incomplete and are not counted.

### Round Safety & Schedule Preview (latest)

- `completed_hizb` now has a unique expression index on `(COALESCE(khatma_id,''), user_id, hizb_number, COALESCE(round_number,1))`. `init_db()` deduplicates legacy rows first; all inserts use `INSERT OR IGNORE`.
- `undo_round()` reopens the most recently closed round from its `khatma_rounds.details` snapshot. It restores pending assignments and only the completions that existed *before* the close, discards the abandoned next round, resets `rotation_finished`, and can be repeated to walk further back. Snapshots now store `completed_before` and `auto_completed`.
- `POST /api/admin/round/undo` exposes it; the admin board shows “↩️ تراجع عن الإغلاق” whenever a round has been closed.
- `get_rotation_schedule()` computes the full planned reader → hizb matrix for every round without writing to the DB. `GET /api/admin/rotation/schedule` serves it (admin PIN header).
- The “📅 جدول التدوير” modal shows the round/member matrix, per-round coverage warnings, per-member totals, CSV export (UTF-8 BOM) and a print view.
- The schedule has three views (`🧮 مصفوفة` / `📅 حسب الدورة` / `👤 حسب القارئ`). Above 12 active readers it defaults to “حسب الدورة”, because a 15+ column matrix is unreadable and too wide.
- Schedule numbers use the `.hizb-nums` helper (`direction:ltr; unicode-bidi:isolate; white-space:nowrap`) so RTL does not reorder “1, 2, 3”. Do **not** reuse the existing `.hizb-list` class: it carries `margin-bottom: 40px` for the reader list and inflates table rows to 73px.
- `printSchedule()` is view-aware: portrait for round/member cards, landscape for the matrix; cards use `break-inside: avoid`.
- Warnings cover short coverage (`<60` per round), members reading fewer than 60 hizbs, and members reading more than 60 (repeats).

## Outstanding Considerations

- Consider adding more real-time updates via WebSocket
- Potential for improved mobile responsiveness
- May need pagination optimization for large Khatmas
- Rotation assumes 60 hizbs per day of capacity; if total quotas exceed 60 the board reports `shortfall` instead of silently dropping readers.
