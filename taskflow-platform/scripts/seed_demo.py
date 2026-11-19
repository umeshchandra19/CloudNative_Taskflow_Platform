"""Create demo users/projects/tasks:  python scripts/seed_demo.py   (uses DATABASE_URL like the app)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import Config  # noqa: E402
from app.db import Database  # noqa: E402
from app.schema import init_schema  # noqa: E402
from app.seed import DEMO_PASSWORD, seed_demo  # noqa: E402

cfg = Config()
database = Database(cfg.database_url, pool_max=2)
init_schema(database)
if seed_demo(database, cfg):
    print(f"Created alice/bob/carol/dave @demo.local  (password: {DEMO_PASSWORD})")
else:
    print("Demo data already present, nothing to do")
database.close()
