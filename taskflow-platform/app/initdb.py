"""python -m app.initdb  - create tables, then optionally seed. Run before starting the web server."""
from .config import Config
from .db import Database
from .schema import init_schema
from .seed import ensure_admin, seed_demo


def main():
    cfg = Config()
    database = Database(cfg.database_url, pool_max=2)
    init_schema(database)
    print("schema ready")
    if ensure_admin(database, cfg):
        print(f"admin {cfg.admin_email} created")
    if cfg.seed_demo and seed_demo(database, cfg):
        print("demo data created (alice/bob/carol/dave @demo.local, password demo-pass-123)")
    database.close()


if __name__ == "__main__":
    main()
