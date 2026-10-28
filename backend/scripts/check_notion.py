"""
Notion journal diagnostic - run after setting NOTION_API_KEY (+ NOTION_DATABASE_ID
or NOTION_PARENT_PAGE_ID) in backend/.env.

    python scripts/check_notion.py            # checks the key, finds/creates the database, adds missing columns
    python scripts/check_notion.py --test     # also writes a test trade page (then moves it to the trash)

Exit code 0 = ready, 1 = something needs fixing (the message explains what).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import ENV_FILE, get_settings  # noqa: E402
from app.db.database import Database  # noqa: E402
from app.db.models import JournalEntry, now_ms  # noqa: E402
from app.services.notion_journal import NotionAuthError, NotionError, NotionJournal, parse_notion_id  # noqa: E402


def fail(message: str) -> int:
    print(f"\n[FAIL] {message}")
    return 1


async def run(write_test: bool) -> int:
    print("=" * 64)
    print(" Notion journal diagnostic")
    print("=" * 64)
    print(f".env file        : {ENV_FILE} ({'found' if ENV_FILE.exists() else 'NOT FOUND'})")
    try:
        settings = get_settings()
    except Exception as exc:
        return fail(f"Your .env file has an invalid value: {exc}")
    key = settings.notion_api_key
    shown = ("set (" + key[:4] + "…" + key[-4:] + ")") if len(key) > 10 else ("set" if key else "EMPTY")
    print(f"NOTION_API_KEY   : {shown}")
    print(f"Database         : {parse_notion_id(settings.notion_database_id) or '-'}")
    print(f"Parent page      : {parse_notion_id(settings.notion_parent_page_id) or '-'}")
    if not settings.notion_configured:
        return fail("Set NOTION_API_KEY and NOTION_DATABASE_ID (or NOTION_PARENT_PAGE_ID). See TUTORIAL.md, Step 16.")

    # Use a throw-away database so the diagnostic never touches your real journal
    tmp = Path(tempfile.mkdtemp())
    db = Database(f"sqlite:///{(tmp / 'check.db').as_posix()}")
    db.create_all()
    journal = NotionJournal(settings, db)
    try:
        try:
            await journal._setup()  # noqa: SLF001 - diagnostic
        except NotionAuthError as exc:
            return fail(exc.message)
        except NotionError as exc:
            return fail(f"Notion error {exc.status} {exc.code}: {exc.message}")
        st = journal.state
        print(f"[OK] Connected   : database '{st['database_title']}'  {st.get('database_url') or ''}")
        if st["skipped_properties"]:
            print(f"[WARN] These columns exist with another type and will not be filled: {', '.join(st['skipped_properties'])}")
        print("[OK] Columns     : all journal columns are present")

        if write_test:
            entry = JournalEntry(
                id=f"check-{uuid.uuid4().hex[:8]}", created_at=now_ms(), updated_at=now_ms(), provider="simulated",
                symbol="TEST:CHECK", timeframe="M15", side="long", status="closed", entry_price=100.0, quantity=1,
                exit_price=101.0, entry_time=now_ms() // 1000 - 900, exit_time=now_ms() // 1000, fees=0.0,
                setup="Diagnostic", tags=["test"], notes="Written by scripts/check_notion.py", notion_status="pending",
            )
            with db.session() as s:
                s.add(entry)
            await journal._sync_one(entry.id)  # noqa: SLF001
            with db.session() as s:
                row = s.get(JournalEntry, entry.id)
                page_id, url = row.notion_page_id, row.notion_url
            if not page_id:
                return fail("The test page was not created")
            print(f"[OK] Test page   : {url}")
            await journal._request("PATCH", f"/v1/pages/{page_id}", json={"in_trash": True})  # noqa: SLF001
            print("[OK] Test page moved to the Notion trash")
    finally:
        await journal.stop()
        db.dispose()

    print("\nNotion is ready. Trades you log in the app's Journal tab will appear in this database.")
    return 0


def main() -> int:
    try:
        return asyncio.run(run("--test" in sys.argv[1:]))
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    sys.exit(main())
