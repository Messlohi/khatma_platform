# Todo List

## Current Tasks
- [x] Fix time rendering in activity feed on `templates/khatma.html` (pass `"timestamp"` in API and use it in JS)
- [x] Fix `NULL` timestamps in `mark_all_done` in `app.py` by specifying `datetime('now')` explicitly in the batch SQL inserts
- [x] Run a one-off database update script to fill existing `NULL` timestamps in `completed_hizb` and `hizb_assignments` to avoid empty displays for existing entries
- [x] Add daily-rotation khatma mode with configurable hizbs per person, step and duration
- [x] Add bulk participant entry by name, with optional per-person quota (`name: N`)
- [x] Warn admins about unassigned hizbs, quota shortfall and quotas below the rotation step
- [x] Add configurable EOD closing (auto vs. admin confirmation) and `/api/cron/eod`
- [x] Verify rotation end-to-end in the browser (bulk add, quota edit, warnings, creation form)
- [x] Make rotation period configurable (`rotation_interval_days`, default 1) instead of one round per day
- [x] Honour `auto_complete_eod` on manual round close and rename “إغلاق اليوم” to “إغلاق الدورة”
- [x] Add 3-day-round regression tests to `rotation_test.py` and run the full backend suite
- [x] Verify configurable interval in the browser (status card, next close date, manual close)
- [x] Guard against duplicate completions with a unique index and `INSERT OR IGNORE`
- [x] Add “تراجع عن الإغلاق” to reopen the last round from its snapshot
- [x] Add a rotation schedule preview with coverage warnings, CSV export and print view
- [x] Add schedule view switcher (matrix / by round / by reader) with a by-round default above 12 readers
- [x] Fix schedule readability for many readers: LTR-isolated hizb numbers, nowrap cells, view-aware print orientation
- [x] Regression tests for duplicate guard, undo, schedule and auth (`enhancements_test.py`)

All tasks completed:

- [x] Read memory bank and understand current state
- [x] Read app.py to understand current hizb selection logic
- [x] Add database migration for selection_type in khatmas table
- [x] Add database migration for activity_log table
- [x] Modify hizb selection logic to support Sahm (random) and Manual modes
- [x] Add activity logging for reserve/return/complete actions
- [x] Test the implementation

## Feature Implementation Details

### Flexible Khatma Types

- Added `selection_type` column to `khatmas` table
- Supports values: 'sequential' (default), 'range', 'sahm', 'manual'
- API endpoints to get/set selection type
- New `/api/join/sahm` endpoint for random selection
- Sahm mode randomly assigns available hizbs to users

### Audit Log

- Added `activity_log` table to track all actions
- Logs: user_id, user_name, action, hizb_number, timestamp, details
- Actions logged: 'reserved', 'returned', 'completed', 'uncompleted'
- API endpoints to retrieve activity logs per khatma or globally

### Daily Rotation Khatmas

- `mode='rotation'` with `hizbs_per_person`, `rotation_step`, `rotation_days`, `rotation_start_date`, `eod_time`
- `khatma_members.slot` defines seat order; round `n` shifts seats by `rotation_step`
- `khatma_rounds` records closed days; `current_round` and `rotation_finished` live on `khatmas`
- `require_eod_confirm` disables automatic closing; `auto_complete_eod` completes pending hizbs at close
- `POST /api/cron/eod` processes due rounds (header `X-Dev-Key` or `?key=`)
- Bulk names + per-person quotas via `parse_participants()` and `POST /api/admin/members/add`
- `rebalance_round()` / `fill_round_gaps()` keep the board consistent after mid-round edits
- Admin board surfaces `unassigned_hizbs`, `shortfall` and `quota_warnings`
- `rotation_interval_days` sets round length (default 1); `rotation_days` is the number of rounds
- `_round_end()` / `get_eod_status()` compute round boundaries and total programme length
- Manual “إغلاق الدورة” respects `auto_complete_eod`; pending hizbs are only auto-completed when enabled
- `undo_round()` restores a round from `khatma_rounds.details` (pending assignments + pre-close completions)
- `get_rotation_schedule()` builds the full planned schedule; CSV/print are rendered client-side
- `completed_hizb` is protected by a unique index; all writes use `INSERT OR IGNORE`
