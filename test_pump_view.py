"""대시보드 펌프 신호 가공 단위 테스트.  실행: python -m unittest -v"""
import unittest
from datetime import datetime, timedelta

import pandas as pd

from pump_view import on_intervals, pump_state, pump_stats

T0 = datetime(2026, 12, 1, 0, 0, 0)


def frame(vals_out1, vals_fb, step=5):
    ts = [T0 + timedelta(seconds=i * step) for i in range(len(vals_out1))]
    return pd.DataFrame({"ts": ts, "out1": vals_out1, "fb": vals_fb})


class IntervalTest(unittest.TestCase):
    def test_basic(self):
        df = frame([0, 1, 1, 1, 0, 1], [0] * 6)
        iv = on_intervals(df["ts"], df["out1"])
        self.assertEqual(iv, [(T0 + timedelta(seconds=5), T0 + timedelta(seconds=20)),
                              (T0 + timedelta(seconds=25), T0 + timedelta(seconds=25))])

    def test_gap_breaks_interval(self):
        ts = pd.Series([T0, T0 + timedelta(seconds=5), T0 + timedelta(seconds=300)])
        iv = on_intervals(ts, pd.Series([1, 1, 1]))
        self.assertEqual(len(iv), 2)


class StateTest(unittest.TestCase):
    def test_waiting_then_fail(self):
        df = frame([0, 1, 1], [0, 0, 0])
        row = df.iloc[-1]
        st = pump_state(row, df, True, 60, now=T0 + timedelta(seconds=30))
        self.assertTrue(st.run_text.startswith("기동 확인 중"))
        st = pump_state(row, df, True, 60, now=T0 + timedelta(seconds=120))
        self.assertTrue(st.run_text.startswith("기동 실패"))
        self.assertEqual(st.level, "alarm")

    def test_running_and_no_fb(self):
        df = frame([1, 1], [1, 1])
        self.assertEqual(pump_state(df.iloc[-1], df, True, 60, now=T0).run_text, "운전 중")
        self.assertEqual(pump_state(df.iloc[-1], df, False, 60, now=T0).run_text, "피드백 미연결")

    def test_uncommanded(self):
        df = frame([0, 0], [1, 1])
        self.assertEqual(pump_state(df.iloc[-1], df, True, 60, now=T0).run_text, "명령 없이 운전 중")

    def test_stats(self):
        df = frame([0, 1, 1, 0, 1, 1, 1], [0, 0, 1, 0, 0, 1, 1])
        starts, run = pump_stats(df, True)
        self.assertEqual(starts, 2)
        self.assertAlmostEqual(run, (5 + 5) / 60)


if __name__ == "__main__":
    unittest.main()
