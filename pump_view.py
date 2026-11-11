"""대시보드용 펌프 신호 가공 (화면과 분리해 단위 시험 가능하게 함).

- on_intervals : 0/1 신호를 'ON 구간(시작~끝)' 목록으로 변환
- pump_state   : 최신 값 + 구간 정보로 카드에 보일 기동명령/운전상태 판정
- pump_stats   : 선택 기간의 기동 횟수·운전 시간
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

MAX_GAP_SEC = 60   # 연속 데이터 사이 간격이 이보다 크면 구간을 끊음(수집 중단 구간을 ON으로 칠하지 않기 위함)


def on_intervals(ts: pd.Series, vals: pd.Series, max_gap: float = MAX_GAP_SEC) -> list[tuple[datetime, datetime]]:
    """값이 1(True)인 연속 구간 [(시작, 끝), ...]. NULL은 OFF로 취급."""
    out: list[tuple[datetime, datetime]] = []
    start = prev = None
    for t, v in zip(ts, vals):
        on = bool(v) if pd.notna(v) else False
        if start is not None and prev is not None:
            gap = (t - prev).total_seconds()
            if not on or gap > max_gap:
                out.append((start, prev if gap > max_gap else t))
                start = None
        if on and start is None:
            start = t
        prev = t
    if start is not None:
        out.append((start, prev))
    return out


@dataclass
class PumpState:
    """카드 표시용 펌프 상태."""
    cmd_text: str        # 기동명령 표시
    cmd_color: str       # CSS 변수명
    run_text: str        # 운전상태 표시
    run_color: str
    level: str           # "" | warn | alarm  (카드 상단 색)


def pump_state(row: pd.Series, pdf: pd.DataFrame, has_fb: bool, start_timeout: int,
               now: datetime | None = None) -> PumpState:
    """최신 행(row)과 해당 포인트 추이(pdf)로 기동명령·운전상태를 판정."""
    now = now or datetime.now()
    out1, fb = row.get("out1"), row.get("fb")

    # 기동명령
    cmd_since = None
    if pd.notna(out1) and out1:
        iv = on_intervals(pdf["ts"], pdf["out1"]) if not pdf.empty else []
        cmd_since = iv[-1][0] if iv else pd.to_datetime(row["ts"])
        mins = int((now - cmd_since).total_seconds() // 60)
        cmd = (f"ON · {mins}분째" if mins else "ON", "--accent")
    elif pd.notna(out1):
        cmd = ("OFF", "--muted")
    else:
        cmd = ("알 수 없음", "--alarm")

    # 운전상태
    level = ""
    if not has_fb:
        run = ("피드백 미연결", "--muted")
    elif pd.isna(fb):
        run, level = ("피드백 통신 두절", "--alarm"), "alarm"
    elif pd.notna(out1) and out1 and fb:
        run = ("운전 중", "--ok")
    elif pd.notna(out1) and out1 and not fb:
        wait = int((now - cmd_since).total_seconds()) if cmd_since is not None else 0
        if wait < start_timeout:
            run, level = (f"기동 확인 중 · {wait}초", "--warn"), "warn"
        else:
            run, level = (f"기동 실패 · {wait // 60}분 {wait % 60}초", "--alarm"), "alarm"
    elif fb:
        run, level = ("명령 없이 운전 중", "--warn"), "warn"
    else:
        run = ("정지", "--muted")
    return PumpState(cmd[0], cmd[1], run[0], run[1], level)


def pump_stats(pdf: pd.DataFrame, has_fb: bool) -> tuple[int, float]:
    """기간 내 (기동명령 횟수, 운전 시간[분]). 피드백이 없으면 명령 ON 시간을 운전 시간으로 대신 계산."""
    if pdf.empty:
        return 0, 0.0
    starts = len(on_intervals(pdf["ts"], pdf["out1"]))
    col = "fb" if has_fb else "out1"
    run = sum((e - s).total_seconds() for s, e in on_intervals(pdf["ts"], pdf[col])) / 60
    return starts, run


def timeline_frame(df: pd.DataFrame, names: dict, fb_points: set[str]) -> pd.DataFrame:
    """타임라인 차트용 구간 표. 행 = 포인트(1줄), 신호 = 기동명령/운전상태."""
    rows = []
    for pid, pdf in df.groupby("point_id", sort=False):
        lanes = [("기동명령", "out1")] + ([("운전상태", "fb")] if pid in fb_points else [])
        for lane, col in lanes:
            for s, e in on_intervals(pdf["ts"], pdf[col]):
                rows.append({"행": names.get(pid, pid), "신호": lane, "start": s,
                             "end": max(e, s + pd.Timedelta(seconds=5)),   # 아주 짧은 구간도 보이도록
                             "분": round((e - s).total_seconds() / 60, 1)})
    return pd.DataFrame(rows, columns=["행", "신호", "start", "end", "분"])
