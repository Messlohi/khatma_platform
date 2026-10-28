# Code Map

## Core Application

- **[app.py](app.py:1)**: Main application file containing:
  - DatabaseManager class (lines 45-800+)
  - Flask app setup and routes
  - Telegram bot handlers
  - API endpoints

## Feature Locations

### Database Layer

- **[DatabaseManager](app.py:45)**: All database operations
  - `init_db()`: Schema creation and migrations
  - `register_web_user()`: User registration with Arabic normalization
  - `assign_hizb()` / `unassign_hizb()`: Hizb management
  - `get_status()`: Khatma progress tracking
  - `get_khatma_rules()` / `set_khatma_rules()`: rotation + EOD configuration (`rotation_interval_days` = round length)
  - `get_khatma_members()` / `set_khatma_members()` / `update_khatma_member()`: seats, quotas, active flag
  - `add_khatma_members_by_names()`: bulk participant creation from pasted names
  - `initialize_rotation()` / `assign_rotation_round()`: start and advance the rotation
  - `close_round()` / `process_due_rounds()` / `get_eod_status()` / `_round_end()`: round-boundary handling for configurable periods
  - `undo_round()`: reopen the last closed round from its snapshot
  - `get_rotation_schedule()`: planned reader → hizb matrix for every round (no DB writes)
  - `get_khatma_assignment_board()`: admin board data, including warnings
  - `rebalance_round()` / `fill_round_gaps()`: repair mid-round quota changes
- **[parse_participants()](app.py:35)**: Parses pasted names and optional `name: N` quotas

### Telegram Bot

- **[start](app.py:830)**: Welcome message handler
- **[join_khatma](app.py:838)**: Reserve hizb command
- **[done_hizb](app.py:918)**: Mark hizb complete
- **[return_hizb](app.py:912)**: Return hizb
- **[status](app.py:925)**: View Khatma status

### Web Routes

- **API Routes** (lines 980+):
  - `/api/khatma/create`: Create new Khatma (accepts `rules` and bulk `participants`)
  - `/api/khatma`: Get Khatma status
  - `/api/join`: Reserve hizb via web
  - `/api/done`: Mark hizb complete
  - `/api/login`: Web user authentication

- **Rotation Admin API** (lines 2776+):
  - `/api/admin/assignment_board`: Seats, quotas and coverage warnings
  - `/api/admin/assignment/save` / `assignment/fill`: Manual and automatic distribution
  - `/api/admin/rotation/start`: Start the rotation
  - `/api/admin/round/close`: Admin-confirmed end of round
  - `/api/admin/round/undo`: Reopen the last closed round
  - `/api/admin/rotation/schedule`: Full planned schedule for preview/export
  - `/api/admin/rules`: Update rotation/EOD rules
  - `/api/admin/member/update`: Toggle active state or per-person quota
  - `/api/admin/members/add`: Bulk add participants (with optional quotas)
  - `/api/cron/eod`: Process due end-of-day rounds (requires dev key)

- **Developer Dashboard** (lines 1029+):
  - `/developer`: Admin dashboard UI
  - `/api/dev/stats`: Global statistics
  - `/api/dev/khatmas`: List all Khatmas

### Templates

- **[templates/khatma.html](templates/khatma.html:1)**: Main Khatma UI (full-featured)
- **[templates/khatma_simple.html](templates/khatma_simple.html:1)**: Simplified Khatma UI
- **[templates/developer.html](templates/developer.html:1)**: Developer/admin dashboard
- **[templates/index.html](templates/index.html:1)**: Landing page

### Static Assets

- **static/css/**: Stylesheets
- **static/js/**: JavaScript files
- **static/og_*.png**: Open Graph images

## Utility Scripts

- **[migrate.py](migrate.py:1)**: Database migration runner
- **[fix_duplicates.py](fix_duplicates.py:1)**: Fix duplicate user entries
- **[test_delete_user.py](test_delete_user.py:1)**: User deletion testing
