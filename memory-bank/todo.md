# Todo List

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
