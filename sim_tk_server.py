"""가상 TK4S 온도조절기 시뮬레이터 (Modbus RTU over TCP).

실제 장비 없이 수집기·경보·대시보드를 시험하기 위한 도구.
- slave 1, 2 두 대를 흉내낸다.
- 온도는 '하루 기온 변화'를 period_sec 동안 압축해 -4 ~ +10 ℃ 사이를 오르내린다.
- 조절기 동작(ON-OFF 제어)도 흉내낸다: PV ≤ on_c → OUT1 ON, PV ≥ off_c → OUT1 OFF, PV ≤ al1_c → AL1 ON.
- --fault-after N : N초 뒤 slave 2를 센서 단선(OPEN) 상태로 만든다.
- 펌프 운전 피드백용 DI 모듈(slave 10)도 흉내낸다: DI0=펌프#1 운전, DI1=펌프#2 운전.
  기동명령 후 --start-delay 초 뒤 운전 ON, 명령 해제 후 3초 뒤 OFF.
- --pump-fail-after N : N초 뒤부터 펌프 #2가 기동명령을 받아도 돌지 않음(기동 실패 시나리오).
- --ecr-port P : P-LTE 구성 모사. ECR LAN I/O(Modbus TCP, unit 1)를 별도 포트로 띄운다.
    coil 16·17 = ECR 릴레이(현장 OUT1을 2초 지연 미러링, P2P 모사)
    coil 0·1   = GSP 보조접점(펌프 운전) — 이때 펌프는 ECR 릴레이를 따라 운전한다.
- --link-fail-after N : N초 뒤 LTE 두절 모사 → ECR 릴레이가 마지막 상태로 멈춤(전달 불일치 시나리오).

실행:
    python sim_tk_server.py --port 5020 --period 600
    (config.yaml 의 modbus.transport 를 rtu_over_tcp 로 바꾸고 수집기 실행)
"""
from __future__ import annotations

import argparse
import asyncio
import math
import time

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.framer import FramerType
from pymodbus.server import ModbusTcpServer

FC_CO, FC_DI, FC_IR = 1, 2, 4


def _u16(v: int) -> int:
    """부호 있는 정수 → 16비트 레지스터 값."""
    return v & 0xFFFF


def make_slave() -> ModbusSlaveContext:
    """TK 주소 공간을 가진 슬레이브 1대 생성.
    (pymodbus 3.8은 get/setValues 양쪽에서 주소+1 처리하므로 프로토콜 주소를 그대로 써도 일치한다)"""
    return ModbusSlaveContext(
        di=ModbusSequentialDataBlock(0, [0] * 32),
        ir=ModbusSequentialDataBlock(0, [0] * 1100),
        co=ModbusSequentialDataBlock(0, [0] * 32),
    )


async def updater(ctx: ModbusServerContext, ecr: ModbusServerContext | None, period: float,
                  fault_after: float | None, on_c: float, off_c: float, al1_c: float,
                  start_delay: float, pump_fail_after: float | None, link_fail_after: float | None) -> None:
    """1초마다 온도·출력·(ECR 릴레이)·펌프 운전 상태 갱신."""
    t0 = time.monotonic()
    out1 = {1: False, 2: False}
    relay = {1: False, 2: False}        # ECR 릴레이 (ecr 미사용 시 OUT1과 동일 취급)
    out1_since = {1: 0.0, 2: 0.0}       # OUT1이 마지막으로 바뀐 시각
    running = {1: False, 2: False}
    changed_at = {1: 0.0, 2: 0.0}       # 펌프를 실제로 구동하는 명령이 마지막으로 바뀐 시각
    offset = {1: 0.0, 2: -1.5}          # 2번 배관이 조금 더 춥다고 가정
    while True:
        t = time.monotonic() - t0
        for sid in (1, 2):
            pv = 3.0 + 7.0 * math.cos(2 * math.pi * t / period) + offset[sid]
            fault = fault_after is not None and sid == 2 and t >= fault_after

            # 조절기 ON-OFF 제어 흉내 (센서 단선 시 조절기는 출력 OFF로 가정)
            prev = out1[sid]
            if fault:
                out1[sid] = False
            elif pv <= on_c:
                out1[sid] = True
            elif pv >= off_c:
                out1[sid] = False
            if out1[sid] != prev:
                out1_since[sid] = t

            # ECR 릴레이: P2P로 2초 뒤 미러링, LTE 두절 시 마지막 상태 유지
            link_down = link_fail_after is not None and t >= link_fail_after
            prev_cmd = relay[sid] if ecr is not None else prev
            if ecr is None:
                cmd = out1[sid]
            else:
                if not link_down and t - out1_since[sid] >= 2:
                    relay[sid] = out1[sid]
                cmd = relay[sid]
            if cmd != prev_cmd:
                changed_at[sid] = t

            # 펌프 실제 운전 상태 흉내 (기동 지연, 정지 지연, 기동 실패)
            broken = pump_fail_after is not None and sid == 2 and t >= pump_fail_after
            if cmd and not broken and t - changed_at[sid] >= start_delay:
                running[sid] = True
            elif not cmd and t - changed_at[sid] >= 3:
                running[sid] = False

            raw_pv = 31000 if fault else round(pv * 10)
            slave = ctx[sid]
            slave.setValues(FC_IR, 0x03E8, [_u16(raw_pv), 1, 0, _u16(round(on_c * 10))])
            bits = [False] * 8
            bits[0] = out1[sid]                          # OUT1 (0x0003)
            bits[6] = (not fault) and pv <= al1_c        # AL1  (0x0009)
            bits[7] = fault                              # AL2  (0x000A): 센서 단선 경보로 설정 가정
            slave.setValues(FC_DI, 0x0003, bits)
        ctx[10].setValues(FC_DI, 0, [running[1], running[2]])   # 피드백 DI 모듈(RS485)
        if ecr is not None:
            io = ecr[1]
            io.setValues(FC_CO, 0, [running[1], running[2]])      # GSP 보조접점 → DI
            io.setValues(FC_CO, 16, [relay[1], relay[2]])         # ECR 릴레이 상태
        await asyncio.sleep(1)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5020)
    ap.add_argument("--period", type=float, default=600, help="온도 1주기(초)")
    ap.add_argument("--fault-after", type=float, default=None, help="N초 뒤 2번 센서 단선")
    ap.add_argument("--on", type=float, default=3.0)
    ap.add_argument("--off", type=float, default=6.0)
    ap.add_argument("--al1", type=float, default=1.0)
    ap.add_argument("--start-delay", type=float, default=8, help="기동명령 후 운전까지 지연(초)")
    ap.add_argument("--pump-fail-after", type=float, default=None, help="N초 뒤 펌프#2 기동 실패")
    ap.add_argument("--ecr-port", type=int, default=None, help="ECR LAN I/O(Modbus TCP) 포트")
    ap.add_argument("--link-fail-after", type=float, default=None, help="N초 뒤 LTE 두절 모사")
    a = ap.parse_args()

    ctx = ModbusServerContext(slaves={1: make_slave(), 2: make_slave(), 10: make_slave()}, single=False)
    ecr = ModbusServerContext(slaves={1: make_slave()}, single=False) if a.ecr_port else None
    asyncio.create_task(updater(ctx, ecr, a.period, a.fault_after, a.on, a.off, a.al1,
                                a.start_delay, a.pump_fail_after, a.link_fail_after))
    servers = [ModbusTcpServer(ctx, framer=FramerType.RTU, address=("127.0.0.1", a.port))]
    print(f"가상 TK4S 2대 + 피드백 DI 모듈(slave 10): 127.0.0.1:{a.port} (RTU over TCP)")
    if ecr is not None:
        servers.append(ModbusTcpServer(ecr, framer=FramerType.SOCKET, address=("127.0.0.1", a.ecr_port)))
        print(f"가상 ECR LAN I/O: 127.0.0.1:{a.ecr_port} (Modbus TCP)")
    await asyncio.gather(*(srv.serve_forever() for srv in servers))


if __name__ == "__main__":
    asyncio.run(main())
