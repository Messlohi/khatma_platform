# Tech Context

## Backend

- **Language**: Python 3
- **Framework**: Flask (web server)
- **Telegram Bot**: python-telegram-bot library
- **Database**: SQLite with WAL mode enabled
- **Async**: asyncio for Telegram bot handling

## Frontend

- **Templates**: Jinja2 (Flask)
- **Styling**: Custom CSS with responsive design
- **Language**: Arabic (RTL support)
- **JavaScript**: Vanilla JS for interactivity

## Key Dependencies

```
flask
python-telegram-bot
gunicorn
```

## Database Schema

- `khatmas`: Multi-tenant Khatma instances
- `users`: User profiles (Telegram + Web)
- `hizb_assignments`: Active hizb reservations
- `completed_hizb`: Completed readings
- `settings`: Khatma configuration
- `intentions`: Niyyah/dedications
- `groups`: Legacy Telegram group support

## Configuration

- Environment variables for production (PythonAnywhere)
- Proxy configuration for free tier hosting
- Developer access key for admin dashboard
