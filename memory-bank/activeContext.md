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

## Outstanding Considerations

- Consider adding more real-time updates via WebSocket
- Potential for improved mobile responsiveness
- May need pagination optimization for large Khatmas
