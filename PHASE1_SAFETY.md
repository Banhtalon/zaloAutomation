# Phase 1 Safety Runbook

## Current safety mode

BCNB writes are disabled by default. With no extra configuration, both Zalo
report updates and LMS confirmation jobs run in dry-run mode and only report
what they would change.

To explicitly enable real BCNB writes after validating a dry run, set this in
the local `.env` file and restart the application:

```env
BCNB_DRY_RUN=false
```

Do not put credentials in `.env.example`. The real `.env`, browser profile,
database, screenshots, uploads and backups are excluded by `.gitignore`.

## LMS workflow

1. Upload the screenshot containing classes that have **not** completed LMS.
2. Select **Phân tích ảnh**.
3. Review the week, the exact classes that will not be ticked, and the exact
   classes planned for ticking.
4. Confirm the one-use plan.
5. Before any action, the server reloads BCNB and rejects the plan if the week,
   table structure, class list or checkbox state changed.

Empty OCR output, invalid codes, unknown codes, duplicate BCNB rows, changed
table headers and stale/replayed confirmations all stop without clicking.

## Browser job rule

Zalo scan, Zalo discovery and LMS BCNB work share one in-process browser lock.
Run Uvicorn with one worker only. A multi-worker deployment requires a database
or file-backed distributed lease before it is safe.

## Verification

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Rollback

The pre-change archive is stored under `.backups/`. It contains sensitive local
credentials and the authenticated browser profile, so keep it private. Extract
it into a separate folder first and verify it before replacing the working
directory.

Credentials that appeared in an older example file must be rotated manually in
the BCNB administration system; changing the repository cannot revoke them.
