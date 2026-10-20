"""Stardust technical architecture, with the subscriber EAC marketplace plugin.

Draws stardust-technical-architecture.png (light) and -dark.png. Spec v1.4 layout, plus:
  * a fifth subscriber-side client: the EAC Marketplace Plugin (OS plugin)
  * a Subscriber Credit Path band: Net Impact Ledger -> EAC claim builder ->
    EAC marketplaces & registries, with credits and revenue returning to the subscriber.

Layout is in "design units": 2000 wide, rendered at 1.25 px per unit (2500 px wide).
"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

W, H = 2000, 2170
SCALE = 1.25
DPI = 100
PT = 72 / DPI * SCALE  # points per design unit

# Usage: python stardust_architecture_diagram.py [dark|light] [output.png]
THEME = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("dark", "light") else "dark"

if THEME == "dark":
    BG, WHITE, SUB, TITLE = "#0d1017", "#f3f6f4", "#d9e1dc", "#f3f6f4"
    C = {
        "sub":   dict(fill="#3d3416", edge="#e8b52b", title=WHITE, label="#e8b52b"),
        "eac":   dict(fill="#2c2142", edge="#b48cff", title=WHITE, label="#c4a3ff"),
        "ing":   dict(fill="#13223b", edge="#69a8f2", title=WHITE),
        "core":  dict(fill="#1a3a2b", edge="#8fdcb2", title=WHITE),
        "mcp":   dict(fill="#3e2b18", edge="#eca452", title="#f2ad62", label="#f2ad62"),
        "pif":   dict(fill="#13422d", edge="#84dca8", title="#8fe2b4", label="#8fe2b4"),
        "drv":   dict(fill="#13422d", edge="#84dca8", title=WHITE),
        "psys":  dict(fill="#17382b", edge="#9fe0bf", title=WHITE),
    }
else:
    BG, WHITE, SUB, TITLE = "#ffffff", "#333333", "#333333", "#0b4d2c"
    C = {
        "sub":   dict(fill="#fcf1e1", edge="#a87b0a", title=WHITE, label="#a87b0a"),
        "eac":   dict(fill="#f1eafc", edge="#6b3fb5", title=WHITE, label="#6b3fb5"),
        "ing":   dict(fill="#e8f0fa", edge="#1e4b8a", title=WHITE),
        "core":  dict(fill="#eeeeee", edge="#555555", title=WHITE),
        "mcp":   dict(fill="#fdf0e1", edge="#a8590a", title="#a8590a", label="#a8590a"),
        "pif":   dict(fill="#e7f3ea", edge="#1f7a50", title="#1f7a50", label="#1f7a50"),
        "drv":   dict(fill="#e7f3ea", edge="#1f7a50", title=WHITE),
        "psys":  dict(fill="#eeeeee", edge="#555555", title=WHITE),
    }

fig = plt.figure(figsize=(W * SCALE / DPI, H * SCALE / DPI), dpi=DPI)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, W)
ax.set_ylim(H, 0)
ax.axis("off")
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)


def fs(units):
    return units * PT


def box(x0, y0, x1, y1, style, lw=3.0):
    s = C[style]
    ax.add_patch(FancyBboxPatch((x0, y0), x1 - x0, y1 - y0,
                                boxstyle="round,pad=0,rounding_size=12",
                                linewidth=lw, edgecolor=s["edge"], facecolor=s["fill"]))


def text(x, y, s, size, color=WHITE, weight="normal", style="normal", ha="center", va="center", **kw):
    ax.text(x, y, s, fontsize=fs(size), color=color, fontweight=weight, fontstyle=style,
            ha=ha, va=va, family="DejaVu Sans", linespacing=1.35, **kw)


def arrow(x0, y0, x1, y1, color, lw=3.0):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=28,
                                 color=color, linewidth=lw, shrinkA=0, shrinkB=0))


def line(xs, ys, color, lw=3.0):
    ax.plot(xs, ys, color=color, linewidth=lw, solid_capstyle="round")


def titled(x0, y0, x1, y1, style, title, sub, tsize=19, ssize=15, tcolor=None, gap=36):
    box(x0, y0, x1, y1, style)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    text(cx, cy - gap / 2 - 6, title, tsize, color=tcolor or C[style]["title"], weight="bold")
    text(cx, cy + gap / 2 + 4, sub, ssize, color=SUB)


# ---- title -------------------------------------------------------------------
text(W / 2, 68, "Stardust Technical Architecture: Ingestion, Middleware, MCP, Provider Drivers & EAC Credits",
     26, color=TITLE, weight="bold")

# ---- subscriber-side clients (5) ---------------------------------------------
text(W / 2, 132, "SUBSCRIBER-SIDE CLIENTS", 17, color=C["sub"]["label"], weight="bold")
X0, X1, GAP = 50, 1910, 22
bw = (X1 - X0 - 4 * GAP) / 5
clients = [
    ("sub", "Chat Plugin", "Claude / ChatGPT / Gemini\n(connector, REST client)"),
    ("sub", "Browser Extension", "Chrome / Edge / Firefox / Safari\n(REST client)"),
    ("sub", "Cloud / Infra Agent", "AWS / Azure / GCP sidecar\n(REST / OTLP client)"),
    ("sub", "Database Agent", "Query/compute instrumentation\n(REST client)"),
    ("eac", "EAC Marketplace Plugin", "OS plugin · eco-positive action\nclaims & credits (REST client)"),
]
centers = []
for i, (style, t, s) in enumerate(clients):
    x0 = X0 + i * (bw + GAP)
    titled(x0, 157, x0 + bw, 313, style, t, s, tsize=19, ssize=14.5, gap=50)
    centers.append(x0 + bw / 2)
for i, cx in enumerate(centers):
    col = C["eac"]["edge"] if i == 4 else C["sub"]["edge"]
    arrow(cx, 313, cx, 410, col)
text(centers[4] + 12, 362, "action\nevidence", 12.5, color=C["eac"]["label"], weight="bold", ha="left")

# ---- ingestion API -------------------------------------------------------------
box(50, 410, 1910, 544, "ing")
text(980, 456, "STARDUST INGESTION API  (REST / gRPC / OTLP)", 19.5, weight="bold")
text(980, 494, "Auth, usage & telemetry event schema (§7, §12, §20, §22.4) — same contract for Subscribers and Providers",
     15, color=SUB)

# ---- core + MCP ---------------------------------------------------------------
arrow(661, 544, 661, 627, C["ing"]["edge"])
text(661, 585, "events", 14, color=C["ing"]["edge"], weight="bold",
     bbox=dict(facecolor=BG, edgecolor="none", pad=2))
box(50, 627, 1275, 990, "core")
text(661, 722, "MIDDLEWARE / BIG DATA & TELEMETRY CORE", 19.5, weight="bold")
text(661, 815, "Event queue + Telemetry Data Store\nRight-Size & Efficiency scoring engine\n"
               "ROI + Return-on-Impact calculator\nNet Impact Ledger (carbon, electricity, water, heat, materials)\n"
               "Remediation broker + registry & market connectors", 17.5, color=SUB)

text(1621, 597, "MCP SERVERS (§23)", 17, color=C["mcp"]["label"], weight="bold")
mcp = [
    (627, "Subscriber Reporting", "Read-only · reporting.read\nusage, impact, right-size, ledger, quotes"),
    (752, "Provider Insights", "Read-only · provider.read\ndemand, orders, issuance, telemetry health"),
    (877, "Operator Lifecycle", "Confirmed writes · operator.lifecycle.*\nasset records · never directory-listed"),
]
for y0, t, s in mcp:
    box(1333, y0, 1910, y0 + 110, "mcp")
    text(1621, y0 + 23, t, 16.5, color=C["mcp"]["title"], weight="bold")
    text(1621, y0 + 70, s, 13.5, color=SUB)
    arrow(1333, y0 + 30, 1277, y0 + 30, C["mcp"]["edge"])
text(1303, 798, "query\nAPI", 13, color=C["mcp"]["label"], weight="bold",
     bbox=dict(facecolor=BG, edgecolor="none", pad=1))
text(1621, 1015, "No telemetry capture, no credit purchase or retirement (§9.5)", 13.5, color=SUB, style="italic")

# ---- NEW: subscriber credit path (EAC) ----------------------------------------
BAND = 250
text(1621, 1075, "SUBSCRIBER CREDIT PATH (EAC)", 17,
     color=C["eac"]["label"], weight="bold")
box(760, 1105, 1275, 1295, "eac")
text(1017, 1140, "EAC CLAIM BUILDER", 18, weight="bold")
text(1017, 1218, "Packages a subscriber's verified\neco-positive actions: on-site renewables,\n"
                 "heat reuse, water recycling, recycling\nLike-for-like · never double-counted", 13.5, color=SUB)
box(1333, 1105, 1910, 1295, "eac")
text(1621, 1140, "EAC MARKETPLACES & REGISTRIES", 18, weight="bold")
text(1621, 1218, "RECs · GOs · I-RECs · granular certificates\nWater benefit certificates\n"
                 "Carbon & plastic credits\nListing, issuance & sale to buyers", 13.5, color=SUB)
arrow(1017, 990, 1017, 1105, C["eac"]["edge"])
text(1025, 1046, "verified positive impact\n(Net Impact Ledger)", 12.5, color=C["eac"]["label"], weight="bold", ha="left")
arrow(1275, 1200, 1333, 1200, C["eac"]["edge"])
text(1304, 1170, "list", 12.5, color=C["eac"]["label"], weight="bold")
# credits & revenue back to the subscriber's EAC plugin, routed up the right margin
line([1910, 1958, 1958], [1250, 1250, 235], C["eac"]["edge"])
arrow(1958, 235, 1912, 235, C["eac"]["edge"])
text(1978, 740, "credits & revenue to subscriber", 13, color=C["eac"]["label"], weight="bold", rotation=90)

# ---- provider-side driver layer (shifted down by BAND) ------------------------
def sy(y):
    return y + BAND

arrow(661, sy(1132), 661, 992, C["pif"]["edge"])
text(661, 1215, "verified telemetry", 14, color=C["pif"]["edge"], weight="bold",
     bbox=dict(facecolor=BG, edgecolor="none", pad=2))
text(980, sy(1101), "PROVIDER-SIDE DRIVER LAYER", 17, color=C["pif"]["label"], weight="bold")
box(50, sy(1132), 1910, sy(1290), "pif")
text(980, sy(1184), "PROVIDER DRIVER INTERFACE", 19.5, color=C["pif"]["title"], weight="bold")
text(980, sy(1222), "Documented adapter schema — CRI / CEP / CCM / MSR fields (§20); REN / WRR / HRC / RCY categories (§22.4)",
     15, color=SUB)
drivers = [("DAC Driver", "Direct Air Capture facilities"), ("Point-Source Driver", "Industrial flue-gas capture"),
           ("Nature-Based Driver", "Biomass / soil / afforestation"), ("Ocean-Based Driver", "Alkalinity / electrochemical")]
dgap = 25
dw = (1910 - 50 - 3 * dgap) / 4
for i, (t, s) in enumerate(drivers):
    x0 = 50 + i * (dw + dgap)
    cx = x0 + dw / 2
    box(x0, sy(1403), x0 + dw, sy(1560), "drv")
    text(cx, sy(1456), t, 19.5, weight="bold")
    text(cx, sy(1493), s, 15, color=SUB)
    arrow(cx, sy(1403), cx, sy(1292), C["drv"]["edge"])
    arrow(cx, sy(1638), cx, sy(1562), C["drv"]["edge"])
box(50, sy(1638), 1910, sy(1795), "psys")
text(980, sy(1691), "PROVIDER SYSTEMS", 19.5, weight="bold")
text(980, sy(1728), "Capture facility hardware / vendor telemetry APIs / sequestration credit registries", 15, color=SUB)

out = sys.argv[2] if len(sys.argv) > 2 else (
    "stardust-technical-architecture-dark.png" if THEME == "dark" else "stardust-technical-architecture.png")
fig.savefig(out, dpi=DPI, facecolor=BG)
print(out)
