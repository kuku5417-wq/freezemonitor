"""경보 판정 엔진.

조절기가 실제 제어(펌프 기동요청)를 하고, 이 모듈은 '감시'만 한다.
판정 항목:
  - COMM_FAIL        : 연속 N회 통신 실패 (조절기 전원 상실·배선 단선 등)
  - SENSOR_FAULT     : PV가 OPEN/HHHH/LLLL (센서 단선·범위 초과)
  - LOW_TEMP         : 저온 경보 (히스테리시스로 해제)
  - OUTPUT_MISMATCH  : 기동 온도 이하인데 OUT1이 일정 시간 꺼져 있음 (조절기 설정 오류 감지)
  - PUMP_ON/PUMP_OFF : OUT1(기동명령) 변화 (정보성 알림)
  [펌프 운전 피드백이 구성된 포인트만]
  - PUMP_START_FAIL  : 기동명령 ON인데 start_timeout_sec 동안 운전 피드백 없음
  - UNCOMMANDED_RUN  : 기동명령 OFF인데 stop_timeout_sec 이상 운전 중 (수동 운전·정지 실패)
  - FB_COMM_FAIL     : 피드백 DI 모듈 연속 N회 읽기 실패
  - PUMP_RUNNING/PUMP_STOPPED : 실제 운전 상태 변화 (정보성 알림)
  [ECR 릴레이(원격 I/O)가 구성된 포인트만 — P-LTE 구성]
  - RELAY_COMM_FAIL  : ECR I/O 연속 N회 읽기 실패
  - CMD_LINK_MISMATCH: 현장 OUT1과 ECR 릴레이가 relay_mismatch_sec 이상 다름 (LTE 구간 기동명령 전달 실패)
경보는 발생/해제 시 1회씩 알리고, 지속 중이면 realert_minutes 주기로 재알림한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from settings import Point, Thresholds
from tk_client import Reading

KIND_LABEL = {
    "COMM_FAIL": "통신 두절",
    "SENSOR_FAULT": "센서 이상",
    "LOW_TEMP": "저온",
    "OUTPUT_MISMATCH": "기동출력 불일치",
    "PUMP_ON": "펌프 기동요청 ON",
    "PUMP_OFF": "펌프 기동요청 OFF",
    "PUMP_START_FAIL": "펌프 기동 실패",
    "UNCOMMANDED_RUN": "명령 없는 펌프 운전",
    "FB_COMM_FAIL": "운전 피드백 통신 두절",
    "PUMP_RUNNING": "펌프 운전 확인",
    "PUMP_STOPPED": "펌프 정지 확인",
    "RELAY_COMM_FAIL": "ECR I/O 통신 두절",
    "CMD_LINK_MISMATCH": "기동명령 전달 불일치",
}


@dataclass
class Event:
    """알림·기록 대상 이벤트."""
    ts: datetime
    point_id: str
    kind: str
    level: str      # ALARM | CLEAR | INFO
    message: str


@dataclass
class _AlarmState:
    active: bool = False
    since: datetime | None = None
    last_sent: datetime | None = None


@dataclass
class _PointState:
    comm_fail: int = 0
    last_out1: bool | None = None
    mismatch_since: datetime | None = None
    fb_comm_fail: int = 0
    last_fb: bool | None = None
    start_wait_since: datetime | None = None   # 기동명령 ON & 운전 OFF 시작 시각
    uncmd_since: datetime | None = None        # 기동명령 OFF & 운전 ON 시작 시각
    relay_comm_fail: int = 0
    link_mismatch_since: datetime | None = None  # 현장 OUT1 ≠ ECR 릴레이 시작 시각
    alarms: dict[str, _AlarmState] = field(default_factory=dict)


class RuleEngine:
    """포인트별 상태를 유지하며 Reading을 받아 Event 목록을 돌려준다."""

    def __init__(self, points: list[Point], th: Thresholds, notify_pump_change: bool = True):
        self.th = th
        self.notify_pump_change = notify_pump_change
        self.names = {p.id: p.name for p in points}
        self.state = {p.id: _PointState() for p in points}

    # ── 내부 도우미 ──
    def _label(self, pid: str) -> str:
        return f"{self.names.get(pid, pid)}({pid})"

    def _set(self, r: Reading, kind: str, cond: bool, detail: str, events: list[Event]) -> None:
        """경보 조건 cond에 따라 발생/재알림/해제 이벤트 생성."""
        st = self.state[r.point_id].alarms.setdefault(kind, _AlarmState())
        name = f"{self._label(r.point_id)} {KIND_LABEL[kind]}"
        if cond and not st.active:
            st.active, st.since, st.last_sent = True, r.ts, r.ts
            events.append(Event(r.ts, r.point_id, kind, "ALARM", f"🚨 {name} 발생 — {detail}"))
        elif cond and st.active:
            if self.th.realert_minutes > 0 and r.ts - st.last_sent >= timedelta(minutes=self.th.realert_minutes):
                st.last_sent = r.ts
                mins = int((r.ts - st.since).total_seconds() // 60)
                events.append(Event(r.ts, r.point_id, kind, "ALARM", f"🔁 {name} 지속 {mins}분 — {detail}"))
        elif not cond and st.active:
            st.active, st.since = False, None
            events.append(Event(r.ts, r.point_id, kind, "CLEAR", f"✅ {name} 해제 — {detail}"))

    def is_active(self, point_id: str, kind: str) -> bool:
        st = self.state[point_id].alarms.get(kind)
        return bool(st and st.active)

    # ── 메인 판정 ──
    def evaluate(self, r: Reading) -> list[Event]:
        ps = self.state[r.point_id]
        th = self.th
        events: list[Event] = []

        # 1) 통신
        if not r.comm_ok:
            ps.comm_fail += 1
            self._set(r, "COMM_FAIL", ps.comm_fail >= th.comm_fail_count,
                      f"연속 {ps.comm_fail}회 응답 없음", events)
            self._relay(r, events)      # 조절기 값이 없어도 ECR I/O 통신은 판정
            self._feedback(r, events)   # 조절기 값이 없어도 피드백 통신·운전상태는 판정
            return events  # 온도·명령 관련 판정 보류(이전 경보 상태 유지)
        ps.comm_fail = 0
        self._set(r, "COMM_FAIL", False, "통신 복구", events)

        # 2) 센서 이상
        self._set(r, "SENSOR_FAULT", r.status != "OK",
                  f"표시 {r.status}" if r.status != "OK" else "정상 값 수신", events)

        # 3) 저온 (센서 정상일 때만, 히스테리시스 해제)
        if r.pv is not None:
            low_active = self.is_active(r.point_id, "LOW_TEMP")
            cond = r.pv <= th.low_alarm_c if not low_active else r.pv < th.low_alarm_clear_c
            self._set(r, "LOW_TEMP", cond, f"현재 {r.pv:.1f} ℃", events)

        # 4) 출력 불일치: 기동 온도 이하인데 OUT1 OFF가 지연시간 이상 지속
        if r.pv is not None and r.pv <= th.pump_on_c and r.out1 is False:
            ps.mismatch_since = ps.mismatch_since or r.ts
        else:
            ps.mismatch_since = None
        mismatch = ps.mismatch_since is not None and \
            (r.ts - ps.mismatch_since).total_seconds() >= th.mismatch_delay_sec
        self._set(r, "OUTPUT_MISMATCH", mismatch,
                  f"현재 {r.pv:.1f} ℃, 기동요청 {'ON' if r.out1 else 'OFF'}" if r.pv is not None else "센서 값 없음",
                  events)

        # 5) 펌프 기동요청 변화 (정보)
        if r.out1 is not None and ps.last_out1 is not None and r.out1 != ps.last_out1 and self.notify_pump_change:
            kind = "PUMP_ON" if r.out1 else "PUMP_OFF"
            temp = f"{r.pv:.1f} ℃" if r.pv is not None else r.status
            events.append(Event(r.ts, r.point_id, kind, "INFO",
                                f"ℹ️ {self._label(r.point_id)} {KIND_LABEL[kind]} (현재 {temp})"))
        if r.out1 is not None:
            ps.last_out1 = r.out1

        # 6) ECR 릴레이(원격 기동명령 전달)
        self._relay(r, events)
        # 7) 펌프 운전 피드백
        self._feedback(r, events)
        return events

    def _relay(self, r: Reading, events: list[Event]) -> None:
        """현장 OUT1이 ECR 릴레이로 제대로 전달되는지 판정 (릴레이 미구성 포인트는 건너뜀)."""
        if r.relay_ok is None:
            return
        ps, th = self.state[r.point_id], self.th
        if not r.relay_ok:
            ps.relay_comm_fail += 1
            self._set(r, "RELAY_COMM_FAIL", ps.relay_comm_fail >= th.comm_fail_count,
                      f"연속 {ps.relay_comm_fail}회 응답 없음 (LTE·라우터·I/O 전원 확인)", events)
            return
        ps.relay_comm_fail = 0
        self._set(r, "RELAY_COMM_FAIL", False, "ECR I/O 통신 복구", events)

        if r.out1 is None:      # 현장 값이 없으면 비교 보류
            return
        if r.out1 != r.relay:
            ps.link_mismatch_since = ps.link_mismatch_since or r.ts
        else:
            ps.link_mismatch_since = None
        dur = (r.ts - ps.link_mismatch_since).total_seconds() if ps.link_mismatch_since else 0
        on = lambda b: "ON" if b else "OFF"
        self._set(r, "CMD_LINK_MISMATCH",
                  ps.link_mismatch_since is not None and dur >= th.relay_mismatch_sec,
                  f"현장 OUT1 {on(r.out1)}, ECR 릴레이 {on(r.relay)} ({int(dur)}초째)" if ps.link_mismatch_since
                  else f"현장·ECR 일치 ({on(r.out1)})", events)

    def _feedback(self, r: Reading, events: list[Event]) -> None:
        """운전 피드백 판정 (피드백 미구성 포인트는 아무것도 하지 않음)."""
        if r.fb_ok is None:
            return
        ps, th = self.state[r.point_id], self.th

        # 피드백 통신
        if not r.fb_ok:
            ps.fb_comm_fail += 1
            self._set(r, "FB_COMM_FAIL", ps.fb_comm_fail >= th.comm_fail_count,
                      f"연속 {ps.fb_comm_fail}회 읽기 실패", events)
            return
        ps.fb_comm_fail = 0
        self._set(r, "FB_COMM_FAIL", False, "피드백 통신 복구", events)

        # 운전 상태 변화 (정보)
        if ps.last_fb is not None and r.fb != ps.last_fb and self.notify_pump_change:
            kind = "PUMP_RUNNING" if r.fb else "PUMP_STOPPED"
            extra = ""
            if r.fb and ps.start_wait_since is not None:
                extra = f", 명령 후 {int((r.ts - ps.start_wait_since).total_seconds())}초"
            events.append(Event(r.ts, r.point_id, kind, "INFO",
                                f"ℹ️ {self._label(r.point_id)} {KIND_LABEL[kind]}{extra}"))
        ps.last_fb = r.fb

        if r.out1 is None:   # 조절기 통신 두절 시 명령 기준 판정 보류
            return

        # 기동 실패: 명령 ON & 운전 OFF 지속
        if r.out1 and not r.fb:
            ps.start_wait_since = ps.start_wait_since or r.ts
        else:
            ps.start_wait_since = None
        wait = (r.ts - ps.start_wait_since).total_seconds() if ps.start_wait_since else 0
        self._set(r, "PUMP_START_FAIL", ps.start_wait_since is not None and wait >= th.start_timeout_sec,
                  f"기동명령 후 {int(wait)}초간 운전신호 없음" if ps.start_wait_since
                  else ("운전 확인" if r.fb else "기동명령 해제"), events)

        # 명령 없는 운전: 명령 OFF & 운전 ON 지속
        if th.stop_timeout_sec > 0 and not r.out1 and r.fb:
            ps.uncmd_since = ps.uncmd_since or r.ts
        else:
            ps.uncmd_since = None
        dur = (r.ts - ps.uncmd_since).total_seconds() if ps.uncmd_since else 0
        self._set(r, "UNCOMMANDED_RUN", ps.uncmd_since is not None and dur >= th.stop_timeout_sec,
                  f"기동명령 OFF 상태로 {int(dur)}초째 운전 중 (수동 운전 또는 정지 실패)" if ps.uncmd_since
                  else "정지 확인" if not r.fb else "기동명령 ON", events)
