# Lessons Learned

## Architectural Quirks

### 1. Arabic Text Normalization

The system implements custom Arabic text normalization in `DatabaseManager.normalize_arabic()`:

- Removes tashkeel (diacritical marks)
- Unifies different forms of Alef (أ, إ, آ)
- Normalizes Ya/Alif Maqsura (ى → ي)
- Normalizes Taa Marbuta (ة → ه)
- Removes invisible characters (ZWSP, LRM, RLM)

**Rule**: Always use `normalize_arabic()` when comparing Arabic user names.

### 2. Race Condition Handling

User registration uses `INSERT OR REPLACE` with retry logic due to potential race conditions when creating users simultaneously.

**Rule**: Always check for existing users before creating new ones in high-concurrency scenarios.

### 3. Multi-Tenant Database Design

The `khatma_id` column is added to most tables to enable multi-tenancy. Legacy tables (`groups`) maintain backward compatibility.

**Rule**: Always pass `khatma_id` when operating on tenant-specific data.

### 4. Timestamp Handling

Timestamps are stored as both Unix floats and ISO strings depending on the table. Migration logic handles type conversion.

**Rule**: Be careful when querying timestamps; check type before comparison.

### 5. Negative User IDs

Web users get negative IDs based on timestamp to avoid collision with Telegram user IDs.

**Rule**: User ID sign indicates the source (positive = Telegram, negative = Web).

## Strict Rules

1. **Database Connections**: Always use context manager `with self.get_connection() as conn:` for database operations.

2. **Khatma ID Required**: All new Khatma operations MUST include `khatma_id` to support multi-tenancy.

3. **Admin Authorization**: Admin actions require both UID verification AND PIN confirmation via `verify_admin_credentials()`.

4. **API Security**: Developer endpoints are protected by `X-Dev-Key` header authentication.

5. **WAL Mode**: Always enable WAL mode (`PRAGMA journal_mode=WAL`) for better concurrency.
