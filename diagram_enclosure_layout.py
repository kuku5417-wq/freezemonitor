"""3안 함체 내부/외부 배치 참고도 생성 스크립트.
실행: python diagram_enclosure_layout.py  ->  enclosure_layout_3an.png 생성

모든 치수는 설계 참고용이며, [추정] 표시된 항목은 공식 확정치가 아닌 추정치이다.
실제 함체 모델 선정 전 반드시 현장/제품 실측으로 재확인할 것 (spec.md §3-1-8, §10 ⑧⑨).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

# 1 unit = 10mm
SCALE = 10.0

COL_BOX = "#ffffff"
COL_EDGE = "#333333"
COL_CONFIRMED = "#1565c0"   # 확정치 (실선)
COL_EST = "#e07b00"         # 추정치 (점선 + 해치, 주황)
COL_WARN = "#c62828"
COL_PT100 = "#2e7d32"


def mm(v):
    return v / SCALE


def box(ax, x, y, w, h, text, fc=COL_BOX, ec=COL_EDGE, fontsize=8.5,
        weight="normal", linestyle="-", hatch=None, lw=1.3, zorder=3):
    b = FancyBboxPatch((x, y), w, h,
                        boxstyle="round,pad=0.03,rounding_size=0.05",
                        linewidth=lw, edgecolor=ec, facecolor=fc,
                        linestyle=linestyle, hatch=hatch, zorder=zorder)
    ax.add_patch(b)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fontsize, weight=weight, zorder=zorder + 1)
    return (x, y, w, h)


def dim_line(ax, p0, p1, text, perp_offset=0.0, fontsize=7.5, color="#555555"):
    """치수선: p0-p1에 수직으로 perp_offset 만큼 떨어진 위치에 양쪽 화살표 + 텍스트."""
    (x0, y0), (x1, y1) = p0, p1
    if abs(x0 - x1) < 1e-6:  # 수직선 -> 가로 오프셋
        ox0, oy0 = x0 + perp_offset, y0
        ox1, oy1 = x1 + perp_offset, y1
    else:  # 수평선 -> 세로 오프셋
        ox0, oy0 = x0, y0 + perp_offset
        ox1, oy1 = x1, y1 + perp_offset
    a = FancyArrowPatch((ox0, oy0), (ox1, oy1), arrowstyle="<->",
                         mutation_scale=8, linewidth=0.9, color=color, zorder=5)
    ax.add_patch(a)
    mx, my = (ox0 + ox1) / 2, (oy0 + oy1) / 2
    if abs(x0 - x1) < 1e-6:
        ax.text(mx + 0.15, my, text, ha="left", va="center", fontsize=fontsize, color=color, rotation=90)
    else:
        ax.text(mx, my + 0.12, text, ha="center", va="bottom", fontsize=fontsize, color=color)


fig = plt.figure(figsize=(14, 10.5), dpi=150)
gs = fig.add_gridspec(2, 2, height_ratios=[3, 1.1], hspace=0.45, wspace=0.25)
ax1 = fig.add_subplot(gs[0, 0])
ax2 = fig.add_subplot(gs[0, 1])
ax3 = fig.add_subplot(gs[1, :])

# ── 함체 외곽 치수 (참고) ────────────────────────────────
ENC_W_MM, ENC_H_MM = 300, 250
enc_w, enc_h = mm(ENC_W_MM), mm(ENC_H_MM)

# ══════════════════════════════════════════════════════════
# 좌: 전면 도어 뷰
# ══════════════════════════════════════════════════════════
ax1.set_xlim(-3, enc_w + 4)
ax1.set_ylim(-7.5, enc_h + 4)
ax1.set_aspect("equal")
ax1.axis("off")
ax1.set_title("전면 도어 뷰 — TK4S ×2 패널 컷아웃", fontsize=12, weight="bold", pad=14)

box(ax1, 0, 0, enc_w, enc_h, "", fc="#f7f9fc", ec=COL_EDGE, lw=1.8)

tk_w, tk_h = mm(96), mm(96)
gap = mm(18)
total_w = tk_w * 2 + gap
start_x = (enc_w - total_w) / 2
cy = (enc_h - tk_h) / 2 + mm(10)
tk1 = box(ax1, start_x, cy, tk_w, tk_h, "TK4S #1\n96×96mm\n컷아웃", fc="#eaf2ff", ec=COL_CONFIRMED, fontsize=8)
tk2 = box(ax1, start_x + tk_w + gap, cy, tk_w, tk_h, "TK4S #2\n96×96mm\n컷아웃", fc="#eaf2ff", ec=COL_CONFIRMED, fontsize=8)

dim_line(ax1, (0, enc_h), (enc_w, enc_h), f"{ENC_W_MM}mm (W)", perp_offset=1.8)
dim_line(ax1, (enc_w, 0), (enc_w, enc_h), f"{ENC_H_MM}mm (H)", perp_offset=1.8)
dim_line(ax1, (start_x, cy), (start_x + tk_w, cy), "96mm", perp_offset=-1.2)
dim_line(ax1, (start_x, cy), (start_x, cy + tk_h), "96mm", perp_offset=-1.2)

# PT100 외벽 부착 지점 (전면 도어 하단, 발열원인 RPi와 반대쪽 — 우측 하단 가정)
pt_x, pt_y = enc_w - mm(20), mm(8)
ax1.scatter([pt_x], [pt_y], s=90, facecolors="none", edgecolors=COL_PT100, linewidths=2, zorder=6)
ax1.text(pt_x, pt_y - mm(16), "PT100 ×2 부착 위치\n(함체 외벽, 발열원 반대쪽)\n[추정] 위치 예시 — 현장 확정 필요",
          ha="center", va="top", fontsize=7.5, color=COL_PT100, weight="bold")

ax1.text(enc_w / 2, -6.8, "실선=확정치(TK4S 96×96mm 컷아웃) · 함체 외곽 300×250mm은 참고 권장값",
          ha="center", fontsize=8, color="#555555")

# ══════════════════════════════════════════════════════════
# 우: 후면 내부 뷰 (백플레이트 배치)
# ══════════════════════════════════════════════════════════
ax2.set_xlim(-3, enc_w + 4)
ax2.set_ylim(-7.5, enc_h + 4)
ax2.set_aspect("equal")
ax2.axis("off")
ax2.set_title("후면 내부 뷰 — 백플레이트 장비 배치", fontsize=12, weight="bold", pad=14)

box(ax2, 0, 0, enc_w, enc_h, "", fc="#f7f9fc", ec=COL_EDGE, lw=1.8)

# RPi 보드 (공식 규격, 확정치 = 실선)
rpi_w, rpi_h = mm(85), mm(56)
rpi_x, rpi_y = mm(15), enc_h - rpi_h - mm(15)
box(ax2, rpi_x, rpi_y, rpi_w, rpi_h, "라즈베리 파이\n85×56mm\n(공식 규격)", fc="#eafaf0", ec=COL_CONFIRMED, fontsize=8, linestyle="-")

# USB-RS485 변환기 (추정치 = 점선 + 해치)
conv_w, conv_h = mm(90), mm(45)
conv_x, conv_y = rpi_x, rpi_y - conv_h - mm(12)
box(ax2, conv_x, conv_y, conv_w, conv_h, "USB-RS485 변환기\n[추정] 약 90×45mm",
    fc="#fff6e8", ec=COL_EST, fontsize=7.8, linestyle=(0, (4, 3)), hatch="///", lw=1.4)

# 27W 전원 어댑터 (추정치)
pw_w, pw_h = mm(60), mm(60)
pw_x, pw_y = rpi_x + rpi_w + mm(20), rpi_y - mm(4)
box(ax2, pw_x, pw_y, pw_w, pw_h, "27W 전원 어댑터\n[추정] 약 60×60mm",
    fc="#fff6e8", ec=COL_EST, fontsize=7.8, linestyle=(0, (4, 3)), hatch="///", lw=1.4)

# 단자대 (추정치, 하단 가로 스트립)
term_w, term_h = mm(100), mm(35)
term_x, term_y = mm(15), mm(10)
box(ax2, term_x, term_y, term_w, term_h, "단자대\n[추정] 약 100×35mm",
    fc="#fff6e8", ec=COL_EST, fontsize=7.8, linestyle=(0, (4, 3)), hatch="///", lw=1.4)

# 최소 이격 거리 표시 (RPi <-> 변환기 사이 세로 간격)
gap_x = rpi_x + mm(5)
dim_line(ax2, (gap_x, conv_y + conv_h), (gap_x, rpi_y), "", perp_offset=0)
ax2.text(rpi_x + rpi_w + 0.3, rpi_y - mm(6), "배선 여유 간격 확보",
          ha="left", va="center", fontsize=7, color="#555555")

# PT100 마운트 지점 (전면 뷰와 동일 상대 위치 — 발열원(RPi) 반대쪽 우측 하단)
pt2_x, pt2_y = enc_w - mm(20), mm(8)
ax2.scatter([pt2_x], [pt2_y], s=90, facecolors="none", edgecolors=COL_PT100, linewidths=2, zorder=6)
ax2.text(pt2_x, pt2_y - mm(16), "PT100 마운트\n(RPi 발열원과 대각선 반대편)",
          ha="center", va="top", fontsize=7.5, color=COL_PT100, weight="bold")

ax2.text(enc_w / 2, -6.8, "실선=확정치 · 점선+해치+주황=[추정] 치수(실측 전 확인 필요)",
          ha="center", fontsize=8, color="#555555")

# ══════════════════════════════════════════════════════════
# 하단: 깊이 간섭 참고 차트
# ══════════════════════════════════════════════════════════
ax3.set_title("함체 내부 깊이 간섭 참고 (전면 돌출 + 후면 돌출 vs 함체 깊이) — 전부 [추정] 치수",
               fontsize=11, weight="bold", pad=10)

categories = ["최소 추정\n(135mm)", "최대 추정\n(200mm)"]
front_vals = [75, 110]     # TK4S 돌출
back_vals = [40, 60]       # 백플레이트 두께
gap_vals = [20, 30]        # 여유 간격
enclosure_depth_ref = 150  # H-05 참고 깊이 하한

y_pos = [0, 1]
ax3.barh(y_pos, front_vals, color="#90a4d4", edgecolor=COL_EDGE, label="TK4S 돌출 깊이(전면)")
ax3.barh(y_pos, back_vals, left=front_vals, color="#f3c177", edgecolor=COL_EDGE, label="백플레이트 장비 두께(후면)")
left2 = [f + b for f, b in zip(front_vals, back_vals)]
ax3.barh(y_pos, gap_vals, left=left2, color="#cfd8dc", edgecolor=COL_EDGE, label="여유 간격(배선)")

ax3.axvline(enclosure_depth_ref, color=COL_WARN, linestyle="--", linewidth=2)
ax3.text(enclosure_depth_ref + 2, 1.55, f"H-05 참고 깊이 하한 {enclosure_depth_ref}mm",
          color=COL_WARN, fontsize=8.5, weight="bold")

for yp, total in zip(y_pos, [sum(x) for x in zip(front_vals, back_vals, gap_vals)]):
    ax3.text(total + 2, yp, f"합계 ~ {total}mm", va="center", fontsize=8.5, weight="bold",
              color=COL_WARN if total > enclosure_depth_ref + 20 else "#333333")

ax3.set_yticks(y_pos)
ax3.set_yticklabels(categories, fontsize=9)
ax3.set_xlabel("깊이 (mm)", fontsize=9)
ax3.set_xlim(0, 230)
ax3.legend(loc="lower right", fontsize=8, frameon=False, ncol=3)
ax3.spines[["top", "right"]].set_visible(False)

fig.suptitle("함체 내부 배치 참고도 (3안) — 전면 도어 / 후면 내부 · 치수는 참고용, [추정] 표시는 확인 필요",
             fontsize=13, weight="bold", y=0.995)

plt.savefig("enclosure_layout_3an.png", dpi=150, bbox_inches="tight")
print("saved enclosure_layout_3an.png")
