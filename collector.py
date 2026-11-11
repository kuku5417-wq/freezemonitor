"""수집기: TK 조절기 폴링 → DB 저장 → 경보 판정 → 알림.

실행:
    python collector.py --config config.yaml
    python collector.py --config config.yaml --once   # 1회만 읽고 종료(설치 점검용)
"""
from __future__ import annotations

import argparse
import logging
import signal
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

from db import Store
from notifiers import build_notifiers, dispatch
from rules import RuleEngine
from settings import load_settings
from tk_client import TKReader

log = logging.getLogger("collector")
_running = True


def _setup_logging(base_dir) -> None:
    """콘솔 + 회전 로그 파일(5MB × 5개)."""
    (base_dir / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(base_dir / "logs" / "collector.log", maxBytes=5_000_000,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [fh, sh]
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # 라이브러리 내부 로그 억제


def _stop(*_):
    """Ctrl+C / 서비스 종료 신호 처리."""
    global _running
    _running = False


def run(config_path: str, once: bool = False) -> None:
    s = load_settings(config_path)
    _setup_logging(s.base_dir)
    store = Store(s.db_path)
    reader = TKReader(s.modbus)
    engine = RuleEngine(s.points, s.thresholds, s.notify_on_pump_change)
    notifiers = build_notifiers(s.notifiers, s.base_dir)
    log.info("수집 시작: 포인트 %d개, 주기 %.0f초, 알림채널 %s",
             len(s.points), s.poll_interval_sec, [n.name for n in notifiers])

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    last_purge = datetime.min

    while _running:
        cycle_start = time.monotonic()
        for p in s.points:
            r = reader.read_point(p)
            store.add_reading(r)
            for ev in engine.evaluate(r):
                store.add_event(ev.ts, ev.point_id, ev.kind, ev.level, ev.message)
                dispatch(notifiers, f"[{s.site_name}] {ev.message}")
            if once:
                log.info("%s: status=%s pv=%s sv=%s out1=%s al1=%s al2=%s fb=%s relay=%s",
                         p.id, r.status, r.pv, r.sv, r.out1, r.al1, r.al2,
                         "미구성" if r.fb_ok is None else r.fb,
                         "미구성" if r.relay_ok is None else r.relay)

        if once:
            break

        # 하루 1회 오래된 데이터 정리
        if (datetime.now() - last_purge).total_seconds() > 86400:
            store.purge(s.retention_days)
            last_purge = datetime.now()

        # 주기 맞추기 (중단 신호에 빨리 반응하도록 0.2초 단위 대기)
        while _running and time.monotonic() - cycle_start < s.poll_interval_sec:
            time.sleep(0.2)

    reader.close()
    store.close()
    log.info("수집 종료")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="동파 방지 온도 감시 수집기")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--once", action="store_true", help="1회만 읽고 결과 출력")
    args = ap.parse_args()
    run(args.config, args.once)
