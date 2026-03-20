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

### Telegram Bot

- **[start](app.py:830)**: Welcome message handler
- **[join_khatma](app.py:838)**: Reserve hizb command
- **[done_hizb](app.py:918)**: Mark hizb complete
- **[return_hizb](app.py:912)**: Return hizb
- **[status](app.py:925)**: View Khatma status

### Web Routes

- **API Routes** (lines 980+):
  - `/api/khatma/create`: Create new Khatma
  - `/api/khatma`: Get Khatma status
  - `/api/join`: Reserve hizb via web
  - `/api/done`: Mark hizb complete
  - `/api/login`: Web user authentication

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
