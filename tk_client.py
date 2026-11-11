"""오토닉스 TK 시리즈 온도조절기 Modbus RTU 읽기 모듈.

주소 맵 (TK 통신 매뉴얼 기준, 0-기반 프로토콜 주소):
  - FC04 Input Register
      0x03E8 PV(현재값, signed)  ─ 특수코드: 31000=OPEN, 30000=HHHH, -30000=LLLL
      0x03E9 소수점 위치(0~3)
      0x03EA 표시 단위(0=℃)
      0x03EB SV(설정값)
  - FC02 Discrete Input
      0x0003 OUT1, 0x0004 OUT2, 0x0009 AL1, 0x000A AL2

※ 감시 SW는 '읽기 전용'이다. 조절기에 쓰기(FC05/06/16)는 하지 않는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from pymodbus.framer import FramerType

from settings import ModbusSettings, Point, RemoteBit

log = logging.getLogger(__name__)

# ── 레지스터 주소 ──
IR_PV_BLOCK = 0x03E8      # PV, 소수점, 단위, SV 4워드 연속 읽기
DI_BLOCK_START = 0x0003   # OUT1 ~ AL2 (0x0003~0x000A) 8비트 연속 읽기
DI_BLOCK_COUNT = 8

# ── PV 특수코드 ──
PV_SPECIAL = {31000: "OPEN", 30000: "HHHH", -30000: "LLLL"}


@dataclass
class Reading:
    """한 번의 폴링 결과."""
    ts: datetime
    point_id: str
    comm_ok: bool
    status: str               # OK | OPEN | HHHH | LLLL | COMM
    pv: float | None = None   # ℃
    sv: float | None = None
    out1: bool | None = None  # 펌프 기동요청 출력
    al1: bool | None = None
    al2: bool | None = None
    fb: bool | None = None    # 펌프 운전 피드백 (None = 미연결 또는 읽기 실패)
    fb_ok: bool | None = None # 피드백 읽기 성공 여부 (None = 피드백 미구성)
    relay: bool | None = None     # ECR I/O 기동 릴레이 상태 (None = 미구성 또는 읽기 실패)
    relay_ok: bool | None = None  # 릴레이 읽기 성공 여부 (None = 미구성)


def _to_signed(v: int) -> int:
    """16비트 무부호 → 부호 있는 정수 변환."""
    return v - 0x10000 if v >= 0x8000 else v


def parse_registers(point_id: str, ts: datetime, regs: list[int], bits: list[bool]) -> Reading:
    """읽어온 레지스터·비트를 Reading으로 해석한다(테스트 용이하도록 분리)."""
    raw_pv = _to_signed(regs[0])
    dot = regs[1] if 0 <= regs[1] <= 3 else 0
    scale = 10 ** dot
    sv = _to_signed(regs[3]) / scale

    status = PV_SPECIAL.get(raw_pv, "OK")
    pv = None if status != "OK" else raw_pv / scale

    return Reading(
        ts=ts, point_id=point_id, comm_ok=True, status=status, pv=pv, sv=sv,
        out1=bool(bits[0]), al1=bool(bits[6]), al2=bool(bits[7]),
    )


class TKReader:
    """RS485 버스 1개에 연결된 TK 조절기들을 읽는 클라이언트."""

    def __init__(self, ms: ModbusSettings):
        self.timeout = ms.timeout
        self._tcp: dict[tuple[str, int], ModbusTcpClient] = {}   # LAN I/O 연결 캐시
        if ms.transport == "serial":
            self.client = ModbusSerialClient(
                port=ms.port, framer=FramerType.RTU, baudrate=ms.baudrate,
                parity=ms.parity, stopbits=ms.stopbits, bytesize=ms.bytesize,
                timeout=ms.timeout, retries=1,
            )
        elif ms.transport == "rtu_over_tcp":
            self.client = ModbusTcpClient(
                host=ms.host, port=ms.tcp_port, framer=FramerType.RTU,
                timeout=ms.timeout, retries=1,
            )
        else:
            raise ValueError("modbus.transport는 serial 또는 rtu_over_tcp 이어야 합니다.")

    def read(self, point: Point) -> Reading:
        """포인트 1개를 읽는다. 실패 시 status='COMM' Reading 반환(예외 전파 안 함)."""
        now = datetime.now()
        try:
            if not self.client.connected:
                self.client.connect()
            rr = self.client.read_input_registers(IR_PV_BLOCK, count=4, slave=point.slave_id)
            if rr.isError():
                raise IOError("input register read error")
            dr = self.client.read_discrete_inputs(DI_BLOCK_START, count=DI_BLOCK_COUNT, slave=point.slave_id)
            if dr.isError():
                raise IOError("discrete input read error")
            return parse_registers(point.id, now, list(rr.registers), list(dr.bits))
        except Exception as exc:  # 통신 예외는 경보로 처리하고 루프는 계속
            log.warning("통신 실패 %s(slave %s): %s", point.id, point.slave_id, type(exc).__name__)
            # 직렬/소켓 상태를 초기화해 다음 폴링에서 재연결되도록 함
            try:
                self.client.close()
            except Exception:
                pass
            return Reading(ts=now, point_id=point.id, comm_ok=False, status="COMM")

    def _read_bit_rtu(self, rb: RemoteBit) -> bool:
        """조절기와 같은 RS485 버스(또는 RTU over TCP)의 장치에서 비트 1개 읽기."""
        if not self.client.connected:
            self.client.connect()
        fn = self.client.read_coils if rb.function == "coils" else self.client.read_discrete_inputs
        rr = fn(rb.address, count=1, slave=rb.slave_id)
        if rr.isError():
            raise IOError("bit read error")
        return bool(rr.bits[0])

    def _read_bit_tcp(self, rb: RemoteBit) -> bool:
        """LAN I/O(Modbus TCP)에서 비트 1개 읽기. 장치별 연결은 재사용한다."""
        key = (rb.host, rb.port)
        cli = self._tcp.get(key)
        if cli is None:
            cli = ModbusTcpClient(host=rb.host, port=rb.port, timeout=self.timeout, retries=1)
            self._tcp[key] = cli
        try:
            if not cli.connected:
                cli.connect()
            fn = cli.read_coils if rb.function == "coils" else cli.read_discrete_inputs
            rr = fn(rb.address, count=1, slave=rb.slave_id)
            if rr.isError():
                raise IOError("bit read error")
            return bool(rr.bits[0])
        except Exception:
            cli.close()   # 다음 폴링에서 재연결
            raise

    def read_bit(self, rb: RemoteBit, what: str) -> bool | None:
        """원격 비트 읽기(반전 적용). 실패 시 None (예외 전파 안 함)."""
        try:
            raw = self._read_bit_tcp(rb) if rb.host else self._read_bit_rtu(rb)
            return raw != rb.invert
        except Exception as exc:
            log.warning("%s 읽기 실패(%s slave %s, addr %s): %s", what,
                        rb.host or "RS485", rb.slave_id, rb.address, type(exc).__name__)
            if not rb.host:
                try:
                    self.client.close()
                except Exception:
                    pass
            return None

    def read_point(self, point: Point) -> Reading:
        """조절기 + (구성된 경우) 운전 피드백·ECR 릴레이를 함께 읽는다."""
        r = self.read(point)
        if point.feedback is not None:
            r.fb = self.read_bit(point.feedback, "피드백")
            r.fb_ok = r.fb is not None
        if point.relay is not None:
            r.relay = self.read_bit(point.relay, "ECR 릴레이")
            r.relay_ok = r.relay is not None
        return r

    def close(self) -> None:
        self.client.close()
        for cli in self._tcp.values():
            cli.close()
