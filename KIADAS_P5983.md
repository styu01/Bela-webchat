# P5.9.8.3 – Telegram Mirror Recovery

- Fix: ambiguous Bot API outcome no longer globally freezes future events.
- At-most-once quarantine of uncertain part; no replay of already attempted chunks or orphan `sending` claims.
- Backoff and read-only `getMe` probe before fresh events; Telegram 429 retry_after.
- Permanent 400/401/403/credential issues retain global block.
- Persistent SQLite meta status: recovery_state, next_retry_at, last_failure_code, failure_streak, last_success_at.
- Read-only HTTP projection exposes queue/quarantine status; Webchat status line shows warnings.
- Installer only touches 3 files. Restarts mirror-only and Webchat HTTP-only; never controls Telegram, worker, broker, Claude or main Marveen.
- Existing P5.9.8.2 custom color panel retained.
