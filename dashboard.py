"""동파 방지 감시 대시보드 (Streamlit, 읽기 전용).

실행:  streamlit run dashboard.py -- --config config.yaml
- DB는 읽기 전용(mode=ro)으로만 연결한다. 제어·설정 변경 기능 없음.
- 10초마다 화면 일부(fragment)만 자동 갱신한다.
- 오류 시 내부 정보(경로·쿼리·스택)를 보여주지 않고 일반 메시지만 표시한다.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from db import load_events, load_latest, load_trend, open_readonly
from pump_view import pump_state, pump_stats, timeline_frame
from rules import KIND_LABEL
from settings import Settings, load_settings

log = logging.getLogger("dashboard")
GENERIC_ERR = "요청을 처리하는 중 오류가 발생했습니다."
STALE_SEC = 60  # 최신 데이터가 이보다 오래되면 '수집 중단'으로 표시

st.set_page_config(page_title="동파 방지 감시", page_icon="❄️", layout="wide")

# ── SHI 공통 CSS (라이트/다크 자동) ──────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&display=swap');
:root { --bg:#F8F9FA; --surface:#FFFFFF; --accent:#004481; --text:#1F2937; --muted:#6B7280;
        --ok:#15803D; --warn:#B45309; --alarm:#B91C1C; --border:#E5E7EB; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0D0F17; --surface:#151929; --accent:#3B82F6; --text:#E5E7EB; --muted:#9CA3AF;
          --ok:#22C55E; --warn:#F59E0B; --alarm:#EF4444; --border:#262B3D; }
}
html, body, [class*="css"] { font-family:'Noto Sans KR','Malgun Gothic',sans-serif; }
.stApp { background:var(--bg); }
.topbar { display:flex; justify-content:space-between; align-items:center;
          border-left:4px solid var(--accent); padding:4px 0 4px 14px; margin-bottom:18px; }
.topbar h1 { font-size:1.45rem; font-weight:700; margin:0; color:var(--text); }
.topbar .sub { color:var(--muted); font-size:.85rem; }
.header-date { color:var(--muted); font-size:.9rem; text-align:right; }
.kpi { background:var(--surface); border:1px solid var(--border); border-top:2px solid var(--accent);
       border-radius:8px; padding:16px 18px; height:100%; }
.kpi.alarm { border-top-color:var(--alarm); } .kpi.warn { border-top-color:var(--warn); }
.kpi .name { color:var(--muted); font-size:.85rem; }
.kpi .val  { font-size:2.3rem; font-weight:700; color:var(--text); line-height:1.2; }
.kpi .meta { color:var(--muted); font-size:.8rem; margin-top:6px; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:2px 10px; border-radius:999px;
        font-size:.78rem; border:1px solid var(--border); margin:6px 6px 0 0; color:var(--text); }
.sig { display:grid; grid-template-columns:72px 1fr; row-gap:6px; margin-top:12px;
       padding-top:10px; border-top:1px dashed var(--border); font-size:.85rem; color:var(--muted); }
.sig .v { color:var(--text); font-weight:700; display:flex; align-items:center; gap:6px; }
.sig .v::before { content:""; width:9px; height:9px; border-radius:50%; background:var(--c); }
.pill::before { content:""; width:7px; height:7px; border-radius:50%; background:var(--c); }
</style>
""", unsafe_allow_html=True)


def _config_path() -> str:
    """'streamlit run dashboard.py -- --config x.yaml' 인자 처리."""
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--config", default="config.yaml")
    args, _ = ap.parse_known_args(sys.argv[1:])
    return args.config


@st.cache_resource
def get_settings(path: str) -> Settings:
    return load_settings(path)


@st.cache_resource
def get_conn(db_path: str):
    return open_readonly(Path(db_path))


@st.cache_data(ttl=5)
def q_latest(db_path: str) -> pd.DataFrame:
    return load_latest(get_conn(db_path))


@st.cache_data(ttl=5)
def q_trend(db_path: str, hours: int) -> pd.DataFrame:
    return load_trend(get_conn(db_path), datetime.now() - timedelta(hours=hours))


@st.cache_data(ttl=5)
def q_events(db_path: str) -> pd.DataFrame:
    return load_events(get_conn(db_path), 200)


def pill(text: str, color_var: str) -> str:
    return f'<span class="pill" style="--c:var({color_var})">{text}</span>'


def relay_row(row: pd.Series) -> tuple[str, str, str]:
    """ECR 릴레이(원격 기동명령) 표시: (문구, 색 변수, 카드 경보 수준)."""
    relay, out1 = row.get("relay"), row.get("out1")
    if pd.isna(relay):
        return "통신 두절", "--alarm", "alarm"
    text = "ON" if relay else "OFF"
    if pd.notna(out1) and bool(out1) != bool(relay):
        return f"{text} · 현장 명령과 다름", "--warn", "warn"
    return text, ("--accent" if relay else "--muted"), ""


def kpi_card(name: str, pid: str, row: pd.Series | None, th, pdf: pd.DataFrame, has_fb: bool,
             has_relay: bool = False) -> str:
    """포인트 1개의 현재 상태 카드 HTML (온도 + 기동명령 + 운전상태)."""
    if row is None:
        return f'<div class="kpi warn"><div class="name">{name} · {pid}</div><div class="val">—</div>' \
               f'<div class="meta">데이터 없음</div></div>'
    ts = pd.to_datetime(row["ts"])
    stale = (datetime.now() - ts).total_seconds() > STALE_SEC
    status = row["status"]
    pills, level = [], ""
    if stale:
        pills.append(pill("수집 중단", "--alarm")); level = "alarm"
    if status == "COMM":
        pills.append(pill("통신 두절", "--alarm")); level = "alarm"
    elif status != "OK":
        pills.append(pill(f"센서 이상 ({status})", "--alarm")); level = "alarm"
    pv = row["pv"]
    if pd.notna(pv):
        if pv <= th.low_alarm_c:
            pills.append(pill("저온", "--alarm")); level = "alarm"
        elif pv <= th.pump_on_c:
            pills.append(pill("기동 온도 이하", "--warn")); level = level or "warn"
        else:
            pills.append(pill("정상", "--ok"))

    ps = pump_state(row, pdf, has_fb, th.start_timeout_sec)
    relay_html = ""
    if has_relay:
        rt, rc, rl = relay_row(row)
        relay_html = f'<span>ECR 릴레이</span><span class="v" style="--c:var({rc})">{rt}</span>'
        if rl == "alarm" or (rl == "warn" and not level):
            level = rl
    if ps.level == "alarm" or (ps.level == "warn" and not level):
        level = ps.level
    starts, run_min = pump_stats(pdf, has_fb)
    run_label = "운전" if has_fb else "명령 ON"

    val = f"{pv:.1f} ℃" if pd.notna(pv) else status
    return (f'<div class="kpi {level}"><div class="name">{name} · {pid}</div>'
            f'<div class="val">{val}</div><div>{"".join(pills)}</div>'
            f'<div class="sig"><span>기동명령</span><span class="v" style="--c:var({ps.cmd_color})">{ps.cmd_text}</span>'
            f'{relay_html}'
            f'<span>운전상태</span><span class="v" style="--c:var({ps.run_color})">{ps.run_text}</span></div>'
            f'<div class="meta">기간 내 기동 {starts}회 · {run_label} {run_min:.0f}분 · 갱신 {ts:%H:%M:%S}</div></div>')


Y_AXIS_W = 130  # 두 차트의 왼쪽 축 폭을 고정해 시간축 위치를 맞춤


def trend_charts(df: pd.DataFrame, names: dict, th, fb_points: set[str], order: list[str],
                 since: datetime) -> tuple[alt.Chart, alt.Chart]:
    """온도 추이 차트, 펌프 기동명령/운전상태 타임라인 차트 (같은 시간축)."""
    # 시간축: 선택 기간 시작과 첫 데이터 중 늦은 시각 ~ 현재 (설치 초기에 빈 구간이 길게 보이지 않도록)
    start = max(since, df["ts"].min().to_pydatetime())
    xdom = alt.Scale(domain=[start.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")])
    d = df[df["status"] == "OK"].copy()
    d["포인트"] = d["point_id"].map(lambda x: f"{names.get(x, x)} ({x})")
    line = alt.Chart(d).mark_line(strokeWidth=2).encode(
        x=alt.X("ts:T", title=None, scale=xdom),
        y=alt.Y("pv:Q", title=None,
                axis=alt.Axis(minExtent=Y_AXIS_W, labelExpr="datum.label + ' ℃'")),
        color=alt.Color("포인트:N", legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("ts:T", title="시각", format="%m-%d %H:%M:%S"), "포인트:N",
                 alt.Tooltip("pv:Q", title="온도", format=".1f")],
    )
    rules = pd.DataFrame({"y": [th.pump_on_c, th.low_alarm_c],
                          "label": [f"기동 {th.pump_on_c:g} ℃", f"저온 경보 {th.low_alarm_c:g} ℃"]})
    rl = alt.Chart(rules).mark_rule(strokeDash=[4, 4], opacity=0.6).encode(y="y:Q")
    tx = alt.Chart(rules).mark_text(align="left", dx=4, dy=-6, opacity=0.7).encode(
        y="y:Q", text="label:N", x=alt.value(0))
    temp = (line + rl + tx).properties(height=300)

    # 펌프 신호 타임라인: 포인트당 1줄
    #  - 기동명령: 넓고 연한 음영(배경 띠)
    #  - 운전상태: 좁고 진한 막대를 그 위에 겹쳐 그림 → 명령 대비 실제 운전 구간이 바로 보임
    tl = timeline_frame(df, names, fb_points)
    rows = [names.get(p, p) for p in order]
    sig_scale = alt.Scale(domain=["기동명령", "운전상태"], range=["#93C5FD", "#1E40AF"])
    base = alt.Chart(tl).encode(
        x=alt.X("start:T", title=None, scale=xdom),
        x2="end:T",
        y=alt.Y("행:N", title=None, sort=rows, scale=alt.Scale(domain=rows),
                axis=alt.Axis(minExtent=Y_AXIS_W, labelLimit=Y_AXIS_W, labelFontSize=12)),
        color=alt.Color("신호:N", scale=sig_scale, legend=alt.Legend(orient="top", title=None)),
        tooltip=["행:N", "신호:N", alt.Tooltip("start:T", title="시작", format="%m-%d %H:%M:%S"),
                 alt.Tooltip("end:T", title="끝", format="%m-%d %H:%M:%S"),
                 alt.Tooltip("분:Q", title="지속(분)")],
    )
    cmd = base.transform_filter(alt.datum.신호 == "기동명령").mark_bar(height=30, opacity=0.45)
    run = base.transform_filter(alt.datum.신호 == "운전상태").mark_bar(height=12, cornerRadius=2)
    bars = alt.layer(cmd, run).properties(height=alt.Step(44))
    return temp, bars


def main() -> None:
    try:
        s = get_settings(_config_path())
    except Exception:
        log.exception("설정 로드 실패")
        st.error(GENERIC_ERR)
        return

    names = {p.id: p.name for p in s.points}

    with st.sidebar:
        st.markdown("### SHI 커미셔닝")
        st.caption(s.site_name)
        st.caption("접속: 사내망 열람 전용 (제어 기능 없음)")
        hours = st.radio("추이 기간", [6, 24, 72, 168], index=1,
                         format_func=lambda h: f"{h}시간" if h < 168 else "7일", key="hours")
        st.divider()
        st.caption(f"기동 ≤ {s.thresholds.pump_on_c:g} ℃ · 정지 ≥ {s.thresholds.pump_off_c:g} ℃\n\n"
                   f"저온 경보 ≤ {s.thresholds.low_alarm_c:g} ℃\n\n"
                   f"기동 확인 제한 {s.thresholds.start_timeout_sec}초")

    st.markdown(f'<div class="topbar"><div><h1>❄️ 동파 방지 온도 감시</h1>'
                f'<div class="sub">{s.site_name}</div></div>'
                f'<div class="header-date">{datetime.now():%Y-%m-%d (%a) %H:%M}</div></div>',
                unsafe_allow_html=True)

    @st.fragment(run_every="10s")
    def live():
        try:
            if not s.db_path.exists():
                st.info("아직 수집된 데이터가 없습니다. 수집기를 먼저 실행하세요.")
                return
            db = str(s.db_path)
            hours = st.session_state.get("hours", 24)
            latest = q_latest(db).set_index("point_id")
            trend = q_trend(db, hours)
            fb_points = {p.id for p in s.points if p.feedback is not None}
            relay_points = {p.id for p in s.points if p.relay is not None}

            cols = st.columns(len(s.points))
            for c, p in zip(cols, s.points):
                row = latest.loc[p.id] if p.id in latest.index else None
                pdf = trend[trend["point_id"] == p.id]
                c.markdown(kpi_card(p.name, p.id, row, s.thresholds, pdf, p.id in fb_points,
                                    p.id in relay_points),
                           unsafe_allow_html=True)

            st.markdown("#### 온도 추이 · 펌프 신호")
            if not fb_points:
                st.caption("운전 피드백이 연결되지 않아 기동명령만 표시합니다. (config.yaml의 feedback 설정 시 운전상태 표시)")
            if trend.empty:
                st.caption("선택한 기간에 데이터가 없습니다.")
            else:
                temp, bars = trend_charts(trend, names, s.thresholds, fb_points,
                                          [p.id for p in s.points], datetime.now() - timedelta(hours=hours))
                st.altair_chart(temp, width="stretch")
                st.altair_chart(bars, width="stretch")

            st.markdown("#### 경보·이벤트 이력")
            ev = q_events(db)
            if ev.empty:
                st.caption("이벤트가 없습니다.")
            else:
                ev = ev.assign(
                    ts=pd.to_datetime(ev["ts"]).dt.strftime("%m-%d %H:%M:%S"),
                    구분=ev["kind"].map(KIND_LABEL).fillna(ev["kind"]),
                    상태=ev["level"].map({"ALARM": "발생", "CLEAR": "해제", "INFO": "정보"}),
                    포인트=ev["point_id"].map(lambda x: names.get(x, x)),
                )[["ts", "포인트", "구분", "상태", "message"]].rename(columns={"ts": "시각", "message": "내용"})
                st.dataframe(ev, hide_index=True, width="stretch", height=320)
        except Exception:
            log.exception("대시보드 갱신 실패")
            st.error(GENERIC_ERR)

    live()


main()
