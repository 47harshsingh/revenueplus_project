"""Rebuild the database from scratch and load the seeded synthetic data.

Everything runs in ONE transaction: schema rebuild, COPY of every table, KPI views,
sanity checks.
Any failure (including a failed sanity check) rolls the whole thing back, so the
database is never left half-loaded.

Usage: DATABASE_URL=postgresql://... python -m data.load
"""

import os
import sys
from pathlib import Path

import pandas as pd
import psycopg

from data.generate import generate

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / "db" / "01_schema.sql"
VIEW_SQL = [ROOT / "db" / "02_views.sql"]  # applied in order, after the data is in
SANITY_SQL = Path(__file__).parent / "sanity.sql"

# FK order: dimensions before facts, deals before activities.
LOAD_ORDER = ["dim_rep", "dim_account", "dim_product", "dim_date",
              "fact_deals", "fact_activities", "ground_truth"]


class SanityCheckFailed(Exception):
    pass


def to_rows(df: pd.DataFrame):
    """DataFrame -> plain Python tuples psycopg can COPY (dates, None for missing)."""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.date
    df = df.astype(object).where(df.notna(), None)
    return df.itertuples(index=False, name=None)


def copy_table(cur: psycopg.Cursor, table: str, df: pd.DataFrame) -> None:
    cols = ", ".join(df.columns)
    with cur.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
        for row in to_rows(df):
            copy.write_row(row)


def load_all(conn: psycopg.Connection, tables: dict[str, pd.DataFrame]) -> None:
    """Rebuild schema, load every table, create KPI views, run sanity checks.

    Runs on the caller's transaction and raises SanityCheckFailed on any mismatch, so
    the caller decides whether to commit. Tests reuse this to build their database.
    """
    with conn.cursor() as cur:
        print("rebuilding schema ...")
        cur.execute(SCHEMA_SQL.read_text())

        for table in LOAD_ORDER:
            copy_table(cur, table, tables[table])
            cur.execute(f"SELECT count(*) FROM {table}")
            loaded = cur.fetchone()[0]
            expected = len(tables[table])
            print(f"  {table:16} {loaded:>7,} rows")
            if loaded != expected:
                raise SanityCheckFailed(f"{table}: loaded {loaded}, generated {expected}")

        for path in VIEW_SQL:
            print(f"applying {path.name} ...")
            cur.execute(path.read_text())

        print("\nsanity checks:")
        cur.execute(SANITY_SQL.read_text())
        failed = []
        for name, actual, expected in cur.fetchall():
            ok = actual == expected
            print(f"  {'PASS' if ok else 'FAIL'}  {name:36} {actual} (expected {expected})")
            if not ok:
                failed.append(name)
        if failed:
            raise SanityCheckFailed(f"{len(failed)} sanity check(s) failed: {failed}")

        cur.execute("ANALYZE")  # fresh planner statistics after a bulk load


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1

    try:
        conn = psycopg.connect(url)
    except (psycopg.ProgrammingError, psycopg.OperationalError) as e:
        # Never echo the URL: it contains the password.
        print("Could not connect with DATABASE_URL. It must be the Supabase *Session pooler* "
              "URI: postgresql://USER:PASSWORD@HOST:5432/postgres (special characters in the "
              "password URL-encoded).", file=sys.stderr)
        print(f"Driver said: {type(e).__name__}: {str(e).splitlines()[0]}", file=sys.stderr)
        return 1

    try:
        with conn:  # commits on success, rolls back on exception
            load_all(conn, generate())
    except SanityCheckFailed as e:
        print(f"\nLOAD ROLLED BACK: {e}", file=sys.stderr)
        return 1

    print("\nload committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
