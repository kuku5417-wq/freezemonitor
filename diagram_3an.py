"""3안(로컬 제어반/스타터 직결) 개략도 생성 스크립트.
실행: python diagram_3an.py  ->  diagram_3an.png 생성
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import matplotlib.font_manager as fm

# Windows 한글 폰트
plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

fig, ax = plt.subplots(figsize=(13, 7.5), dpi=150)
ax.set_xlim(0, 13)
ax.set_ylim(0, 7.5)
ax.axis("off")

COL_FIELD = "#eaf2ff"
COL_ECR = "#fff3e6"
COL_BOX = "#ffffff"
COL_CTRL = "#2e7d32"   # 제어 경로 (녹색, 실선)
COL_MON = "#1565c0"    # 모니터링 경로 (파란색, 점선)
COL_EDGE = "#333333"


def box(x, y, w, h, text, fc=COL_BOX, ec=COL_EDGE, fontsize=10, weight="normal"):
    b = FancyBboxPatch((x, y), w, h,
                        boxstyle="round,pad=0.08,rounding_size=0.08",
                        linewidth=1.3, edgecolor=ec, facecolor=fc, zorder=3)
    ax.add_patch(b)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fontsize, weight=weight, zorder=4)
    return (x, y, w, h)


def arrow(p0, p1, color, style="-", lw=2.2, label=None, label_dy=0.18, fontsize=9):
    a = FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=16,
                         linewidth=lw, color=color, linestyle=style, zorder=2)
    ax.add_patch(a)
    if label:
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2 + label_dy
        ax.text(mx, my, label, ha="center", va="bottom", fontsize=fontsize, color=color)


# ── 영역 배경 ──────────────────────────────────────────────
ax.add_patch(mpatches.FancyBboxPatch((0.3, 0.3), 7.6, 6.9,
             boxstyle="round,pad=0.05,rounding_size=0.15",
             linewidth=1.5, edgecolor="#8ba3c7", facecolor=COL_FIELD, zorder=0))
ax.text(4.1, 7.0, "엔진룸(ER) floor 현장", ha="center", fontsize=13, weight="bold")

ax.add_patch(mpatches.FancyBboxPatch((8.2, 0.3), 4.5, 6.9,
             boxstyle="round,pad=0.05,rounding_size=0.15",
             linewidth=1.5, edgecolor="#c79a5b", facecolor=COL_ECR, zorder=0))
ax.text(10.45, 7.0, "2nd deck ECR", ha="center", fontsize=13, weight="bold")

# ── 현장(ER floor) 구성요소 ───────────────────────────────
pt100 = box(0.7, 5.5, 1.6, 0.8, "PT100 ×2\n(배관 표면온도)", fontsize=9)
tk4s = box(2.9, 5.5, 1.7, 0.8, "TK4S ×2\n(온도조절기)", fontsize=9)
starter = box(5.1, 5.5, 2.3, 0.8, "로컬 스타터반\nRemote Start 단자", fontsize=9)
pump = box(5.5, 4.0, 1.5, 0.7, "순환펌프", fontsize=9)
di = box(5.3, 2.7, 1.9, 0.7, "DI 모듈 (선택)\n운전 피드백", fontsize=8.5)
conv = box(0.7, 2.7, 2.6, 0.7, "RS485→이더넷 변환기\n(고정 IP)", fontsize=8.5)
lte1 = box(0.9, 1.3, 2.2, 0.7, "P-LTE 라우터 #1", fontsize=9)

# ── ECR 구성요소 ───────────────────────────────
lte2 = box(8.5, 1.3, 2.2, 0.7, "P-LTE 라우터 #2", fontsize=9)
pc = box(9.9, 2.7, 2.5, 0.9, "감시 PC\n(수집·대시보드·알림)", fontsize=9)
note = box(8.5, 4.6, 3.9, 1.4,
           "ICMS / GSP 경유 없음\n통신(P-LTE) 두절 시에도\n펌프 기동은 영향받지 않음",
           fc="#fff8e1", fontsize=9, weight="bold")

# ── 화살표: 제어 경로 (녹색 실선, 전부 로컬) ──────────────
arrow((1.5, 5.5), (1.5, 5.5), COL_CTRL)  # placeholder (미사용)
arrow((2.5, 5.9), (2.9, 5.9), COL_CTRL)              # PT100 -> TK4S
arrow((4.6, 5.9), (5.1, 5.9), COL_CTRL, label="OUT1 (a접점)")  # TK4S -> 스타터
arrow((6.25, 5.5), (6.25, 4.7), COL_CTRL, label="기동 명령")   # 스타터 -> 펌프
arrow((6.0, 4.0), (6.0, 3.4), "#8a8a8a", style=(0, (4, 2)),
      label="운전 보조접점(선택)", label_dy=-0.28, fontsize=8)  # 펌프 -> DI (피드백)

# ── 화살표: 모니터링 경로 (파란 점선, RS485 -> P-LTE -> PC) ─
arrow((3.0, 5.5), (2.0, 3.4), COL_MON, style=(0, (5, 2)), label="RS485")   # TK4S -> 변환기
arrow((6.0, 2.7), (3.3, 2.9), COL_MON, style=(0, (5, 2)))                   # DI -> 변환기(버스 합류)
arrow((2.0, 2.7), (2.0, 2.0), COL_MON, style=(0, (5, 2)))                   # 변환기 -> 라우터1
arrow((3.1, 1.65), (8.5, 1.65), COL_MON, style=(0, (5, 2)), lw=2.0,
      label="사설 LTE (모니터링 데이터만, 고정 IP)", fontsize=8.5)          # LTE1 -> LTE2
arrow((9.6, 2.0), (10.8, 2.7), COL_MON, style=(0, (5, 2)))                  # 라우터2 -> PC

# ── 범례 ───────────────────────────────────────────────────
legend_handles = [
    plt.Line2D([0], [0], color=COL_CTRL, lw=2.5, label="제어 경로 (로컬, 유선, 통신 무관)"),
    plt.Line2D([0], [0], color=COL_MON, lw=2.2, linestyle=(0, (5, 2)), label="모니터링 경로 (RS485 + P-LTE, 읽기 전용)"),
    plt.Line2D([0], [0], color="#8a8a8a", lw=2.0, linestyle=(0, (4, 2)), label="피드백 접점 (선택)"),
]
ax.legend(handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, -0.04),
          ncol=3, fontsize=9, frameon=False)

ax.text(4.1, 0.55, "3안: 로컬 제어반(스타터) 직결 — ICMS·GSP 비경유", ha="center",
        fontsize=12, weight="bold", color="#1a1a1a")

plt.tight_layout()
plt.savefig("diagram_3an.png", dpi=150, bbox_inches="tight")
print("saved diagram_3an.png")
