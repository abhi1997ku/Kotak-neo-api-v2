"""
Daily screener pipeline with results persistence.
Runs the swing trade screener and stores results to a database.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict
import logging

try:
    from screener.live_scan import rank_candidates, format_report, real_universe, synthetic_universe, ScreenerResult
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    from live_scan import rank_candidates, format_report, real_universe, synthetic_universe, ScreenerResult

logger = logging.getLogger(__name__)

# Database setup
DB_PATH = Path(__file__).parent.parent / "screener_results.db"


class ScreenerDatabase:
    """Persist screener results to SQLite for historical tracking."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Initialize database tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    mode TEXT,  -- 'real' or 'synthetic'
                    min_rr REAL,
                    candidate_count INTEGER,
                    data_fetched_count INTEGER,
                    status TEXT  -- 'success' or 'error'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_run_id INTEGER,
                    symbol TEXT,
                    setup_type TEXT,
                    entry REAL,
                    stop REAL,
                    target REAL,
                    rr REAL,
                    suggested_qty INTEGER,
                    rationale TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id)
                )
                """
            )
            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")

    def save_scan(
        self,
        candidates: List[ScreenerResult],
        mode: str,
        min_rr: float,
        data_count: int,
    ) -> int:
        """
        Save a scan run and its results to the database.
        Returns the scan_run_id.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Insert scan run
            cursor.execute(
                """
                INSERT INTO scan_runs (mode, min_rr, candidate_count, data_fetched_count, status)
                VALUES (?, ?, ?, ?, 'success')
                """,
                (mode, min_rr, len(candidates), data_count),
            )
            conn.commit()

            scan_run_id = cursor.lastrowid

            # Insert candidates
            for candidate in candidates:
                cursor.execute(
                    """
                    INSERT INTO candidates 
                    (scan_run_id, symbol, setup_type, entry, stop, target, rr, suggested_qty, rationale)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scan_run_id,
                        candidate.symbol,
                        candidate.setup_type,
                        candidate.entry,
                        candidate.stop,
                        candidate.target,
                        candidate.rr,
                        candidate.suggested_qty,
                        candidate.rationale,
                    ),
                )
            conn.commit()

            logger.info(f"Saved scan run {scan_run_id} with {len(candidates)} candidates")
            return scan_run_id

    def get_latest_scan(self, mode: str = "real") -> Dict:
        """Get the most recent scan results."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT * FROM scan_runs WHERE mode = ? ORDER BY scan_date DESC LIMIT 1
                """,
                (mode,),
            )
            scan_run = cursor.fetchone()

            if not scan_run:
                return {}

            cursor.execute(
                """
                SELECT * FROM candidates WHERE scan_run_id = ? ORDER BY rr DESC
                """,
                (scan_run["id"],),
            )
            candidates = cursor.fetchall()

            return {
                "scan_run": dict(scan_run),
                "candidates": [dict(c) for c in candidates],
            }

    def get_scan_history(self, mode: str = "real", days: int = 7) -> List[Dict]:
        """Get scan history for the past N days."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                f"""
                SELECT * FROM scan_runs 
                WHERE mode = ? AND scan_date >= datetime('now', '-{days} days')
                ORDER BY scan_date DESC
                """,
                (mode,),
            )
            return [dict(row) for row in cursor.fetchall()]


def run_daily_scan(mode: str = "real", min_rr: float = 2.0) -> Dict:
    """
    Run the screener and save results to database.
    
    Args:
        mode: 'real' to fetch from Yahoo Finance, 'synthetic' for testing
        min_rr: Minimum risk:reward ratio threshold
        
    Returns:
        Dictionary with scan results
    """
    logger.info(f"Starting daily scan: mode={mode}, min_rr={min_rr}")

    try:
        # Fetch data
        if mode == "real":
            universe = real_universe(days=100)
        else:
            universe = synthetic_universe()

        data_count = len(universe)
        logger.info(f"Fetched data for {data_count} symbols")

        # Run screener
        candidates = rank_candidates(universe, min_rr=min_rr)
        logger.info(f"Found {len(candidates)} candidates")

        # Save to database
        db = ScreenerDatabase()
        scan_run_id = db.save_scan(candidates, mode, min_rr, data_count)

        return {
            "success": True,
            "scan_run_id": scan_run_id,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "report": format_report(candidates),
        }

    except Exception as e:
        logger.error(f"Error running scan: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="Daily screener with persistence")
    parser.add_argument(
        "--mode",
        choices=["real", "synthetic"],
        default="real",
        help="Data source"
    )
    parser.add_argument(
        "--min-rr",
        type=float,
        default=2.0,
        help="Minimum risk:reward ratio"
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Show scan history instead of running a new scan"
    )

    args = parser.parse_args()

    if args.history:
        db = ScreenerDatabase()
        history = db.get_scan_history(mode=args.mode, days=7)
        print(f"\n📊 Scan History (Last 7 days, mode={args.mode})")
        print("=" * 80)
        for scan in history:
            print(
                f"  {scan['scan_date']}: {scan['candidate_count']} candidates, "
                f"{scan['data_fetched_count']} symbols fetched"
            )
    else:
        result = run_daily_scan(mode=args.mode, min_rr=args.min_rr)
        
        if result["success"]:
            print("\n" + result["report"])
            print(f"\n✓ Scan saved with ID: {result['scan_run_id']}")
        else:
            print(f"\n✗ Scan failed: {result.get('error', 'Unknown error')}")
