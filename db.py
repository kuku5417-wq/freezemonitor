"""SQLite 저장소.

- 모든 SQL은 '?' 파라미터 바인딩만 사용 (문자열 조합 금지).
- WAL 모드: 수집기가 쓰는 동안 대시보드가 동시에 읽을 수 있음.
- 대시보드는 open_readonly()로 읽기 전용 연결만 사용.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from tk_client import Reading

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    ts       TEXT NOT NULL,
    point_id TEXT NOT NULL,
    status   TEXT NOT NULL,
    pv       REAL,
    sv       REAL,
    out1     INTEGER,
    al1      INTEGER,
    al2      INTEGER,
    fb       INTEGER,         -- 펌프 운전 피드백 (NULL = 미연결/읽기 실패)
    relay    INTEGER          -- ECR 기동 릴레이 상태 (NULL = 미구성/읽기 실패)
);
CREATE INDEX IF NOT EXISTS ix_readings_point_ts ON readings(point_id, ts);

CREATE TABLE IF NOT EXISTS events (
    ts       TEXT NOT NULL,
    point_id TEXT NOT NULL,
    kind     TEXT NOT NULL,   -- rules.KIND_LABEL 참고
    level    TEXT NOT NULL,   -- ALARM | CLEAR | INFO
    message  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
"""


def _b(v: bool | None) -> int | None:
    """bool → 0/1 (None 유지)."""
    return None if v is None else int(v)


class Store:
    """수집기용 쓰기 저장소."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """이전 버전 DB에 fb 컬럼이 없으면 추가 (컬럼명은 고정 상수라 SQL 조합 위험 없음)."""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(readings)")}
        if "fb" not in cols:
            self.conn.execute("ALTER TABLE readings ADD COLUMN fb INTEGER")
        if "relay" not in cols:
            self.conn.execute("ALTER TABLE readings ADD COLUMN relay INTEGER")
        self.conn.commit()

    def add_reading(self, r: Reading) -> None:
        """폴링 결과 1건 저장."""
        self.conn.execute(
            "INSERT INTO readings (ts, point_id, status, pv, sv, out1, al1, al2, fb, relay) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.ts.isoformat(timespec="seconds"), r.point_id, r.status, r.pv, r.sv,
             _b(r.out1), _b(r.al1), _b(r.al2), _b(r.fb), _b(r.relay)),
        )
        self.conn.commit()

    def add_event(self, ts: datetime, point_id: str, kind: str, level: str, message: str) -> None:
        """경보·이벤트 1건 저장."""
        self.conn.execute(
            "INSERT INTO events (ts, point_id, kind, level, message) VALUES (?,?,?,?,?)",
            (ts.isoformat(timespec="seconds"), point_id, kind, level, message),
        )
        self.conn.commit()

    def purge(self, retention_days: int) -> None:
        """보관 기간이 지난 데이터 삭제."""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
        self.conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
        self.conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


# ── 대시보드(읽기 전용) ─────────────────────────────────────────
def open_readonly(path: Path) -> sqlite3.Connection:
    """읽기 전용 URI 모드로 연결 (대시보드에서 쓰기 불가)."""
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)


def load_latest(conn: sqlite3.Connection) -> pd.DataFrame:
    """포인트별 최신 1건."""
    sql = """
    SELECT r.* FROM readings r
    JOIN (SELECT point_id, MAX(ts) AS mts FROM readings GROUP BY point_id) m
      ON r.point_id = m.point_id AND r.ts = m.mts
    """
    return pd.read_sql_query(sql, conn)


def load_trend(conn: sqlite3.Connection, since: datetime) -> pd.DataFrame:
    """지정 시각 이후 추이 데이터."""
    sql = "SELECT ts, point_id, status, pv, out1, fb, relay FROM readings WHERE ts >= ? ORDER BY ts"
    df = pd.read_sql_query(sql, conn, params=(since.isoformat(timespec="seconds"),))
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def load_events(conn: sqlite3.Connection, limit: int = 200) -> pd.DataFrame:
    """최근 이벤트."""
    sql = "SELECT ts, point_id, kind, level, message FROM events ORDER BY ts DESC LIMIT ?"
    return pd.read_sql_query(sql, conn, params=(int(limit),))
