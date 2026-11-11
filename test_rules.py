"""경보 판정·레지스터 해석 단위 테스트.  실행: python -m unittest -v"""
import unittest
from datetime import datetime, timedelta

from rules import RuleEngine
from settings import Point, Thresholds
from tk_client import Reading, parse_registers

T0 = datetime(2026, 12, 1, 0, 0, 0)
P = [Point("TT-01", "배관 #1", 1)]


def rd(sec, pv=5.0, out1=False, status="OK", comm=True):
    """테스트용 Reading 생성."""
    if not comm:
        return Reading(T0 + timedelta(seconds=sec), "TT-01", False, "COMM")
    return Reading(T0 + timedelta(seconds=sec), "TT-01", True, status,
                   pv if status == "OK" else None, 3.0, out1, False, False)


def kinds(evs):
    return [(e.kind, e.level) for e in evs]


class ParseTest(unittest.TestCase):
    def test_negative_pv_with_decimal(self):
        r = parse_registers("TT-01", T0, [(-25) & 0xFFFF, 1, 0, 30], [True] + [False] * 7)
        self.assertEqual(r.pv, -2.5)
        self.assertEqual(r.sv, 3.0)
        self.assertTrue(r.out1)

    def test_open_code(self):
        r = parse_registers("TT-01", T0, [31000, 1, 0, 30], [False] * 8)
        self.assertEqual(r.status, "OPEN")
        self.assertIsNone(r.pv)

    def test_llll_code(self):
        r = parse_registers("TT-01", T0, [(-30000) & 0xFFFF, 1, 0, 30], [False] * 8)
        self.assertEqual(r.status, "LLLL")


class RuleTest(unittest.TestCase):
    def setUp(self):
        self.e = RuleEngine(P, Thresholds(realert_minutes=60, mismatch_delay_sec=120, comm_fail_count=3))

    def test_low_temp_hysteresis(self):
        self.assertEqual(kinds(self.e.evaluate(rd(0, 0.8, True))), [("LOW_TEMP", "ALARM")])
        self.assertEqual(kinds(self.e.evaluate(rd(5, 1.5, True))), [])        # 해제 기준(2.0) 미만 → 유지
        self.assertEqual(kinds(self.e.evaluate(rd(10, 2.1, True))), [("LOW_TEMP", "CLEAR")])

    def test_realert(self):
        self.e.evaluate(rd(0, 0.5, True))
        self.assertEqual(kinds(self.e.evaluate(rd(1800, 0.5, True))), [])
        self.assertEqual(kinds(self.e.evaluate(rd(3600, 0.5, True))), [("LOW_TEMP", "ALARM")])

    def test_comm_fail_after_n(self):
        self.assertEqual(kinds(self.e.evaluate(rd(0, comm=False))), [])
        self.assertEqual(kinds(self.e.evaluate(rd(5, comm=False))), [])
        self.assertEqual(kinds(self.e.evaluate(rd(10, comm=False))), [("COMM_FAIL", "ALARM")])
        self.assertIn(("COMM_FAIL", "CLEAR"), kinds(self.e.evaluate(rd(15, 5.0))))

    def test_sensor_fault(self):
        self.assertEqual(kinds(self.e.evaluate(rd(0, status="OPEN"))), [("SENSOR_FAULT", "ALARM")])
        self.assertEqual(kinds(self.e.evaluate(rd(5, 5.0))), [("SENSOR_FAULT", "CLEAR")])

    def test_output_mismatch_after_delay(self):
        self.assertEqual(kinds(self.e.evaluate(rd(0, 2.5, False))), [])
        self.assertEqual(kinds(self.e.evaluate(rd(60, 2.5, False))), [])
        self.assertEqual(kinds(self.e.evaluate(rd(120, 2.5, False))), [("OUTPUT_MISMATCH", "ALARM")])
        evs = kinds(self.e.evaluate(rd(125, 2.5, True)))
        self.assertIn(("OUTPUT_MISMATCH", "CLEAR"), evs)
        self.assertIn(("PUMP_ON", "INFO"), evs)

    def test_pump_change_info(self):
        self.e.evaluate(rd(0, 4.0, False))
        self.assertEqual(kinds(self.e.evaluate(rd(5, 3.0, True))), [("PUMP_ON", "INFO")])
        self.assertEqual(kinds(self.e.evaluate(rd(10, 6.0, False))), [("PUMP_OFF", "INFO")])


def rf(sec, out1, fb, pv=2.0, fb_ok=True, comm=True):
    """피드백 포함 Reading."""
    r = rd(sec, pv, out1, comm=comm)
    r.fb, r.fb_ok = (fb if fb_ok else None), fb_ok
    return r


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.e = RuleEngine(P, Thresholds(start_timeout_sec=60, stop_timeout_sec=120, comm_fail_count=3,
                                          low_alarm_c=-50, low_alarm_clear_c=-49))

    def test_start_ok_within_timeout(self):
        self.e.evaluate(rf(0, False, False, pv=4))
        self.assertEqual(kinds(self.e.evaluate(rf(5, True, False))), [("PUMP_ON", "INFO")])
        evs = self.e.evaluate(rf(20, True, True))
        self.assertEqual(kinds(evs), [("PUMP_RUNNING", "INFO")])
        self.assertIn("15초", evs[0].message)

    def test_start_fail_and_recover(self):
        self.e.evaluate(rf(0, True, False))
        self.assertEqual(kinds(self.e.evaluate(rf(30, True, False))), [])
        self.assertEqual(kinds(self.e.evaluate(rf(60, True, False))), [("PUMP_START_FAIL", "ALARM")])
        evs = kinds(self.e.evaluate(rf(70, True, True)))
        self.assertIn(("PUMP_START_FAIL", "CLEAR"), evs)
        self.assertIn(("PUMP_RUNNING", "INFO"), evs)

    def test_uncommanded_run(self):
        self.e.evaluate(rf(0, False, True, pv=8))
        self.assertEqual(kinds(self.e.evaluate(rf(60, False, True, pv=8))), [])
        self.assertEqual(kinds(self.e.evaluate(rf(120, False, True, pv=8))), [("UNCOMMANDED_RUN", "ALARM")])
        evs = kinds(self.e.evaluate(rf(125, False, False, pv=8)))
        self.assertIn(("UNCOMMANDED_RUN", "CLEAR"), evs)
        self.assertIn(("PUMP_STOPPED", "INFO"), evs)

    def test_feedback_comm_fail(self):
        for t in (0, 5):
            self.assertEqual(kinds(self.e.evaluate(rf(t, True, None, fb_ok=False))), [])
        self.assertEqual(kinds(self.e.evaluate(rf(10, True, None, fb_ok=False))), [("FB_COMM_FAIL", "ALARM")])

    def test_no_feedback_configured(self):
        r = rd(0, 2.0, True)   # fb_ok=None → 피드백 판정 없음
        self.assertEqual(kinds(self.e.evaluate(r)), [])
        self.assertEqual(kinds(self.e.evaluate(rd(100, 2.0, True))), [])


def rr(sec, out1, relay, relay_ok=True, pv=2.0, comm=True):
    """ECR 릴레이 포함 Reading."""
    r = rd(sec, pv, out1, comm=comm)
    r.relay, r.relay_ok = (relay if relay_ok else None), relay_ok
    return r


class RelayLinkTest(unittest.TestCase):
    def setUp(self):
        self.e = RuleEngine(P, Thresholds(relay_mismatch_sec=30, comm_fail_count=3,
                                          low_alarm_c=-50, low_alarm_clear_c=-49),
                            notify_pump_change=False)

    def test_mismatch_after_delay_and_clear(self):
        self.assertEqual(kinds(self.e.evaluate(rr(0, True, False))), [])
        self.assertEqual(kinds(self.e.evaluate(rr(20, True, False))), [])
        self.assertEqual(kinds(self.e.evaluate(rr(30, True, False))), [("CMD_LINK_MISMATCH", "ALARM")])
        self.assertEqual(kinds(self.e.evaluate(rr(35, True, True))), [("CMD_LINK_MISMATCH", "CLEAR")])

    def test_short_delay_is_ok(self):
        self.e.evaluate(rr(0, True, False))
        self.assertEqual(kinds(self.e.evaluate(rr(5, True, True))), [])

    def test_relay_comm_fail(self):
        for t in (0, 5):
            self.assertEqual(kinds(self.e.evaluate(rr(t, True, None, relay_ok=False))), [])
        self.assertEqual(kinds(self.e.evaluate(rr(10, True, None, relay_ok=False))), [("RELAY_COMM_FAIL", "ALARM")])
        self.assertEqual(kinds(self.e.evaluate(rr(15, True, True))), [("RELAY_COMM_FAIL", "CLEAR")])

    def test_relay_comm_checked_even_when_field_comm_down(self):
        for t in (0, 5):
            self.e.evaluate(rr(t, None, None, relay_ok=False, comm=False))
        evs = kinds(self.e.evaluate(rr(10, None, None, relay_ok=False, comm=False)))
        self.assertIn(("RELAY_COMM_FAIL", "ALARM"), evs)
        self.assertIn(("COMM_FAIL", "ALARM"), evs)


if __name__ == "__main__":
    unittest.main()
