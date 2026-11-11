"""설정 파일(config.yaml) 로더.

- 설정은 dataclass로 변환해 오타·누락을 실행 초기에 잡는다.
- 상대 경로(DB, 토큰 파일)는 config.yaml이 있는 폴더 기준으로 바꾼다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ModbusSettings:
    """TK4S RS485 통신 설정."""
    transport: str = "serial"
    port: str = "COM3"
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 2
    bytesize: int = 8
    timeout: float = 1.0
    host: str = "127.0.0.1"
    tcp_port: int = 5020


@dataclass
class RemoteBit:
    """원격 I/O의 비트 1개(DI 또는 릴레이 상태).

    - slave_id : 장치 통신주소(Modbus unit id)
    - address  : 비트 주소(0-기반)
    - invert   : b접점(운전 시 열림) 신호면 True
    - host     : 지정하면 Modbus TCP 장치(LAN I/O)로 읽음. 비우면 조절기와 같은 RS485 버스에서 읽음
    - port     : Modbus TCP 포트 (기본 502)
    - function : discrete_inputs(FC02) | coils(FC01)
    """
    slave_id: int
    address: int
    invert: bool = False
    host: str | None = None
    port: int = 502
    function: str = "discrete_inputs"

    def __post_init__(self):
        if self.function not in ("discrete_inputs", "coils"):
            raise ValueError("function은 discrete_inputs 또는 coils 이어야 합니다.")


# 이전 버전 설정과의 호환용 이름
Feedback = RemoteBit


@dataclass
class Point:
    """측정 포인트 1개 = 온도조절기 1대.

    - feedback : 펌프 운전 피드백(GSP 보조접점 등) — 선택
    - relay    : 원격(ECR) I/O의 기동 릴레이 상태 — 선택.
                 지정하면 '현장 OUT1 vs ECR 릴레이'를 비교해 기동명령 전달 실패를 감시한다.
    """
    id: str
    name: str
    slave_id: int
    feedback: RemoteBit | None = None
    relay: RemoteBit | None = None


@dataclass
class Thresholds:
    """경보 판정 기준값."""
    pump_on_c: float = 3.0
    pump_off_c: float = 6.0
    low_alarm_c: float = 1.0
    low_alarm_clear_c: float = 2.0
    mismatch_delay_sec: int = 120
    comm_fail_count: int = 3
    realert_minutes: int = 60
    start_timeout_sec: int = 60   # 기동명령 후 이 시간 안에 운전 피드백이 없으면 '기동 실패'
    stop_timeout_sec: int = 120   # 명령 OFF인데 이 시간 이상 운전 중이면 '명령 없는 운전'(0=판정 안 함)
    relay_mismatch_sec: int = 30  # 현장 OUT1과 ECR 릴레이가 이 시간 이상 다르면 '기동명령 전달 불일치'


@dataclass
class Settings:
    """전체 설정 묶음."""
    site_name: str
    modbus: ModbusSettings
    points: list[Point]
    thresholds: Thresholds
    poll_interval_sec: float
    notify_on_pump_change: bool
    db_path: Path
    retention_days: int
    notifiers: dict = field(default_factory=dict)
    base_dir: Path = Path(".")


def load_settings(path: str | Path) -> Settings:
    """config.yaml을 읽어 Settings 객체로 반환한다."""
    cfg_path = Path(path).resolve()
    base = cfg_path.parent
    with cfg_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)  # safe_load: 임의 객체 생성 방지

    points = []
    for p in raw["points"]:
        fb = p.pop("feedback", None)
        rl = p.pop("relay", None)
        points.append(Point(**p, feedback=RemoteBit(**fb) if fb else None,
                            relay=RemoteBit(**rl) if rl else None))
    ids = [p.id for p in points]
    if len(ids) != len(set(ids)):
        raise ValueError("포인트 id가 중복되었습니다.")

    th = Thresholds(**raw.get("thresholds", {}))
    if th.low_alarm_clear_c <= th.low_alarm_c:
        raise ValueError("low_alarm_clear_c는 low_alarm_c보다 커야 합니다.")
    if th.pump_off_c <= th.pump_on_c:
        raise ValueError("pump_off_c는 pump_on_c보다 커야 합니다.")

    db = raw.get("database", {})
    db_path = Path(db.get("path", "data/freeze.db"))
    if not db_path.is_absolute():
        db_path = base / db_path

    return Settings(
        site_name=raw.get("site_name", "동파 방지 감시"),
        modbus=ModbusSettings(**raw.get("modbus", {})),
        points=points,
        thresholds=th,
        poll_interval_sec=float(raw.get("poll_interval_sec", 5)),
        notify_on_pump_change=bool(raw.get("notify_on_pump_change", True)),
        db_path=db_path,
        retention_days=int(db.get("retention_days", 90)),
        notifiers=raw.get("notifiers", {}) or {},
        base_dir=base,
    )
