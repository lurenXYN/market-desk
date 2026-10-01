"""Runtime configuration for the market desk."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "desk.db"
STATIC_DIR = Path(__file__).resolve().parent / "static"

HOST = "127.0.0.1"
PORT = 8765
# Browser open URL (same as bind host when LAN is off).
BROWSER_HOST = "127.0.0.1"
# Hit East Money / Tencent only in session; idle loop just waits for the next open.
SESSION_REFRESH_SECONDS = 20
IDLE_CHECK_SECONDS = 60
# Sticky mainline: challenger must beat incumbent by this score margin.
MAINLINE_STICKY_MARGIN = 12.0
# Persist a switch only after this gap; flip-flops inside the window are dropped.
MAINLINE_SWITCH_MIN_SECONDS = 300
# Same-theme boards (煤炭↔动力煤) need a larger margin to displace each other.
MAINLINE_THEME_SWITCH_MULT = 2.0
# When sticky is ending/退潮, same-theme challengers skip the theme multiplier.
MAINLINE_THEME_FADE_SKIP = True
# During the hold window, live sticky needs this extra multiple to flip.
MAINLINE_HOLD_SWITCH_MULT = 1.5

# Sibling industry/concept names that should not ping-pong as "mainline changes".
MAINLINE_THEME_GROUPS: tuple[tuple[str, ...], ...] = (
    ("煤炭", "动力煤", "焦煤", "焦炭", "煤化工", "煤炭开采"),
    (
        "半导体",
        "芯片",
        "集成电路",
        "电子元件",
        "电路板",
        "覆铜",
        "PCB",
        "印制电路",
        "消费电子",
        "电子",
    ),
    ("通信", "5G", "光通信", "通信线缆", "通信设备", "通信服务"),
    ("医药", "制药", "医疗", "中药", "生物", "器械", "CXO"),
    ("白酒", "酿酒", "啤酒", "酒类"),
    ("军工", "航天", "航空", "船舶", "国防"),
    ("新能源", "光伏", "锂电", "电池", "储能", "风电"),
    ("有色", "稀土", "黄金", "铜", "铝"),
    ("传媒", "游戏", "影视", "广告"),
    ("银行",),
    ("证券", "券商"),
    ("房地产", "地产", "物业"),
    ("物流", "快递", "航运", "港口"),
    ("电力", "火电", "水电", "电网", "公用"),
    ("农业", "种植", "养殖", "饲料", "农产品", "果蔬", "农药", "化肥", "氮肥", "钾肥"),
    ("教育", "培训"),
)
TOAST_ENABLED = True
TOAST_COOLDOWN_SECONDS = 180
# Flat commission (CNY): once per buy and once per sell (per position / day).
TRADE_FEE_CNY = 5.0

EASTMONEY_UT = "bd1d9ddb04089700cf9c27f6f7426281"
ZT_UT = "7eea3edcaed734bea9cbfc24409ed989"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/ztb/detail",
}

ETF_WATCH = [
    ("sh515050", "515050", "通信ETF"),
    ("sz159819", "159819", "人工智能ETF"),
    ("sh513120", "513120", "港股创新药ETF"),
    ("sh510880", "510880", "红利ETF"),
    ("sh512010", "512010", "医药ETF"),
    ("sh512480", "512480", "半导体ETF"),
    ("sh512880", "512880", "证券ETF"),
    ("sh512660", "512660", "军工ETF"),
    ("sh516160", "516160", "新能源ETF"),
    ("sh512400", "512400", "有色ETF"),
    ("sh512980", "512980", "传媒ETF"),
    ("sh512800", "512800", "银行ETF"),
    ("sh515220", "515220", "煤炭ETF"),
    ("sh512200", "512200", "房地产ETF"),
    ("sh512690", "512690", "酒ETF"),
    ("sh516910", "516910", "物流ETF"),
    ("sh512570", "512570", "证券龙头ETF"),
    ("sz159915", "159915", "创业板ETF"),
    ("sh588000", "588000", "科创50ETF"),
]

# Major A-share indices (Tencent symbol, display code, name).
INDEX_WATCH = [
    ("sh000001", "000001", "上证指数"),
    ("sz399001", "399001", "深证成指"),
    ("sz399006", "399006", "创业板指"),
    ("sh000688", "000688", "科创50"),
    ("sh000300", "000300", "沪深300"),
    ("sh000905", "000905", "中证500"),
    ("sh000016", "000016", "上证50"),
]

# These two ETFs are tradable. ChiNext / STAR stocks are not recommended.
CHINEXT_STAR_ETFS = frozenset({"159915", "588000"})

MAINLINE_ETF_RULES: list[tuple[tuple[str, ...], tuple[str, str, str]]] = [
    (("通信", "5G", "光通信"), ("sh515050", "515050", "通信ETF")),
    (("半导体", "芯片", "集成电路", "存储", "封测", "电子", "电路板", "覆铜", "PCB", "印制电路"), ("sh512480", "512480", "半导体ETF")),
    (
        ("人工智能", "算力", "光模块", "液冷", "服务器", "光学光电子", "软件", "信创", "机器人", "自动驾驶"),
        ("sz159819", "159819", "人工智能ETF"),
    ),
    (("医药", "制药", "医疗", "中药", "生物", "CXO", "器械"), ("sh512010", "512010", "医药ETF")),
    (("白酒", "酿酒", "酒类", "啤酒"), ("sh512690", "512690", "酒ETF")),
    (("证券", "券商"), ("sh512880", "512880", "证券ETF")),
    (("军工", "航天", "航空", "船舶", "国防"), ("sh512660", "512660", "军工ETF")),
    (("新能源", "光伏", "锂电", "电池", "储能", "风电", "电网"), ("sh516160", "516160", "新能源ETF")),
    (("有色", "稀土", "黄金", "铜", "铝"), ("sh512400", "512400", "有色ETF")),
    (("传媒", "游戏", "影视", "广告", "互联网"), ("sh512980", "512980", "传媒ETF")),
    (("银行",), ("sh512800", "512800", "银行ETF")),
    (("煤炭", "焦煤", "动力煤", "焦炭"), ("sh515220", "515220", "煤炭ETF")),
    (("房地产", "地产", "物业"), ("sh512200", "512200", "房地产ETF")),
    (("创新药", "港股"), ("sh513120", "513120", "港股创新药ETF")),
    (("物流", "快递", "航运", "港口", "交运"), ("sh516910", "516910", "物流ETF")),
    (("红利", "高股息", "公用", "电力", "水电", "火电", "石油", "油气"), ("sh510880", "510880", "红利ETF")),
    (("消费", "食品", "饮料", "零售", "商贸"), ("sh510880", "510880", "红利ETF")),
    (("保险", "多元金融"), ("sh512570", "512570", "证券龙头ETF")),
    (("创业板",), ("sz159915", "159915", "创业板ETF")),
    (("科创",), ("sh588000", "588000", "科创50ETF")),
]

PIN_INDUSTRY_ALIASES = {
    "通信": ("通信设备", "通信服务"),
    "医药": ("化学制药", "生物制品", "医疗器械", "中药", "医药商业"),
    "算力观察": ("光学光电子", "半导体"),
}

CONCEPT_JUNK_KEYWORDS = (
    "昨日",
    "涨停",
    "跌停",
    "连板",
    "炸板",
    "ST",
    "融资",
    "融券",
    "沪股通",
    "深股通",
    "标准普尔",
    "富时",
    "MSCI",
    "转融通",
    "预盈",
    "预增",
    "扭亏",
    "亏损",
    "高送转",
    "股权转让",
    "一季报",
    "三季报",
    "年报",
    "中报",
    "中证",
    "中盘",
    "微盘",
    "大盘股",
    "小盘",
    "沪深",
    "上证",
    "深证",
    "指数",
)

HOT_BOARD_COUNT = 8
ICE_BOARD_COUNT = 4
CONSTITUENT_TOP = 20

# Server酱 free tier: 5 pushes per SendKey per day, shared with news-radar
# (09:00 / 13:00 / 21:00 digests). market-desk only pushes the 15:05 EOD
# one-pager; intraday buy / fly / sell / LHB / ops alerts and the 09:25 morning
# brief stay on the page unless these switches are turned back on.
SERVERCHAN_EVENT_PUSH = False
SERVERCHAN_MORNING_PUSH = False

# Soft risk hints for the local position book (not hard blocks).
POSITION_MAX_NAMES = 6
POSITION_MAX_SINGLE_PCT = 35.0
POSITION_MAX_THEME_PCT = 50.0  # soft tip when one theme (by theme_key) ≥ this of book market
POSITION_MAX_TOTAL_COST = 200000.0

# Minimum total market cap (亿元) for main-board stock recommendations; 0 disables.
# 120 is a practical floor for short-term pullbacks; 100 is also fine if you want more names.
MIN_STOCK_MV_YI = 120.0

# Show an observation side branch when its score stays within this gap of the mainline.
SIDE_MAINLINE_GAP = 12.0

# Mute buy/entry noise for this many minutes after 09:30 (0 = off).
OPEN_MUTE_MINUTES = 5

# Buy-side day-high pullback / tip gates (percent).
# Listing band (入池) vs tip distance (ready 确认) are intentionally split.
STOCK_READY_PULLBACK_MIN = 1.0
STOCK_PULLBACK_BAND_MAX = 5.0
STOCK_PULLBACK_SWEET_MAX = 4.2
ETF_OFF_HIGH_MIN = 0.40
STOCK_OFF_HIGH_MIN = 0.75
ETF_NEAR_HIGH_PCT = 0.40
STOCK_NEAR_HIGH_PCT = 0.50
# Buy outcome quality: close green but day-low stabbed / open faded → not a hit.
OUTCOME_FAKE_RED_LOW_PCT = -2.0   # day1 low vs entry ≤ this → 次日虚红
OUTCOME_FAKE_RED_OPEN_PCT = 1.5   # day1 open ≥ this and close weak → 次日冲高回落
OUTCOME_FAKE_RED_CLOSE_MAX = 0.5  # close pct below this counts as "weak close"
# Bump when buy/sell outcome label semantics change; review refresh migrates old rows.
# v5: watch-track sell fly uses day0 open as 09:45 decision-price proxy.
# v6: round pct before label thresholds; deep 次日绿 beats 冲高回落.
# v7: do not lock day1 labels on an in-progress session bar (wait until 15:05).
# v8: ignore any unfinished forward bar; refresh 三日% until day-3 close; wider rescore.
OUTCOME_FORMULA_VERSION = 8
# Review display standards (DB always stores classic).
OUTCOME_STANDARDS = ("classic", "same_day_plan", "filled")
# Review-page hint only (never gates signals): the live-vs-plan premium that
# marks a buy row as chasing.
REVIEW_ABOVE_PLAN_WARN_PCT = 1.0
# Book fill ≥ this % above the buy plan → chase warning on record + review chase-cost.
CHASE_WARN_PCT = 1.5
CHASE_COST_DAYS = 20
# Ready-gate monitor on the review board (display only): lit buys vs same-day
# peers over the most recent scored trade days. Below MIN_N no verdict is shown.
REVIEW_READY_MONITOR_DAYS = 20
REVIEW_READY_MONITOR_MIN_N = 8
REVIEW_READY_MONITOR_EXCESS_PCT = 1.0
# Gate ledger (display only): every gate / flag a buy carried vs peers without it.
# Row gates use same-day excess; market (day-level) gates use gated-vs-ungated raw
# 3-day return. EDGE is the |diff| needed before a gate is called good or harmful.
GATE_LEDGER_DAYS = 20
GATE_LEDGER_MIN_N = 8
GATE_LEDGER_MIN_DAYS = 3
GATE_LEDGER_EDGE_PCT = 0.8
# Post-close fund-flow research snapshot: per-kind board count for the full pull
# (the hot path only keeps the top-80 inflow boards).
EOD_FUND_FLOW_LIMIT = 500
# Review payload cache for today (the page auto-refreshes every 30s in session).
# Daily klines and pending outcome scoring are throttled separately so a fast
# page cadence only re-pulls the single batched live-quote call.
REVIEW_TODAY_CACHE_SEC = 25.0
REVIEW_HEAVY_REFRESH_SEC = 300.0
# Pick compare scoring (display only): base score, and how local history nudges
# rule points — each win-rate pp above/below the overall rate is worth
# PICK_HIST_PP_TO_PTS, capped at ±PICK_HIST_MAX_ADJ, ignored below PICK_HIST_MIN_N
# rows or PICK_HIST_MIN_DAYS distinct sessions. Win rates are day-balanced.
PICK_BASE_SCORE = 60.0
PICK_HIST_MIN_N = 8
PICK_HIST_MIN_DAYS = 5
# 2026-09 audits (scripts/audit_pick_score.py, scripts/optimize_pick_score.py):
# classic trend / board / ready rule points ranked stored buys backwards, and a
# walk-forward test showed the history nudge (bucket win rates from < 20 trading
# days) hurt ranking, so it is off (0) and the win rate is shown as info only.
PICK_HIST_PP_TO_PTS = 0.0
PICK_HIST_MAX_ADJ = 8.0
# 年内无涨停: shown as info only — zero-limit-up names showed no edge on either sample.
PICK_ZT_NONE_PTS = 0.0
PICK_MAX_ITEMS = 6
# Review score column: today's rows are re-scored at most this often; during the
# session a background pass runs every REVIEW_SCORE_BG_SEC so the first score
# (payload.pick0, shown on past days) is captured close to signal time.
REVIEW_SCORE_CACHE_SEC = 45.0
REVIEW_SCORE_BG_SEC = 180.0
# Chip-peak / daily-volume context (prior-day bars only). Offline audit on
# 2026-09 buys: entry 5–15% above chip average cost or on ≥80% profit chips
# ran ~7–11pp below the base 3-day win rate; a quiet prior day ran ~+7–11pp;
# 3-day volume fade ~-10pp. Rule points stay small; history nudges on top.
CV_CHIP_WINDOW = 120
CV_CHIP_MIN_BARS = 60
CV_CHIP_BINS = 200
CV_FETCH_CONCURRENCY = 4
CV_TICK_TIMEOUT_S = 6.0
# Kept small: on ~15k uptrend-pullback days across the signaled universe the
# chip / volume buckets showed no measurable 3-day edge.
PICK_CV_CHIP_HIGH_PTS = -2.0
PICK_CV_VOL_SHRINK_PTS = 0.0
PICK_CV_VOL_SPIKE_PTS = 0.0
PICK_CV_VOL_FADE_PTS = -2.0
# Idiosyncratic volatility: std of (stock pct - ChiNext index pct) over the 20
# days before the signal. The one borrowed factor that held on both the ~15k
# pullback days (IC -0.07, t -5; top 20% win rate ~-2pp, bottom 20% ~+2pp) and
# the stored buys (scripts/research_regime_factors.py).
CV_IVOL_INDEX = "sz399006"
CV_IVOL_WINDOW = 20
CV_IVOL_MIN_DAYS = 15
CV_IVOL_HIGH = 4.2
CV_IVOL_LOW = 2.0
PICK_CV_IVOL_HIGH_PTS = -3.0
PICK_CV_IVOL_LOW_PTS = 2.0
# Float market cap from the prior bar (amount / turnover). Small caps held on
# both samples (scripts/research_untested_factors.py): stored buys IC -0.16,
# smallest third +0.8~1.1% same-day excess; ~16k pullback days smallest fifth
# +0.41% / +2.2pp win rate, the rest flat.
CV_SMALL_CAP_YI = 80.0
PICK_CV_SMALL_CAP_PTS = 3.0
# Watch-only tags (0 points). Open gap ≥2%: stored buys IC -0.19 but no edge on
# the big sample. Billboard within the prior N sessions: weaker 3-day outcome on
# both samples (-1.6~-2.3% / -0.35%), yet a penalty did not improve ranking.
PICK_GAP_WATCH_PCT = 2.0
PICK_LHB_LOOKBACK_DAYS = 5
PICK_LHB_CACHE_SEC = 1800.0
# Lifecycle boards frozen from the last close but missing from today's hot list:
# fetch at most this many separately per tick so their cards show live data.
LIFECYCLE_SIDE_MAX = 8
# Future sell open watch window (minutes after 09:30); used by TODO 开盘卖出缓冲窗.
SELL_OPEN_WATCH_MINUTES = 15
# Soft: during open watch, "must" stop that only barely lost open may wait (false break).
SELL_OPEN_SHALLOW_BREAK_PCT = 0.45

# Wait price as a fraction of last (shallower wait → nearer entry tags).
ETF_WAIT_GAP = 0.9955   # ~0.45% below last
STOCK_WAIT_GAP = 0.9915  # ~0.85% below last
# Relative band around suggested buy for「回踩到位」.
ETF_NEAR_ENTRY_UP = 0.0045   # was 0.55%; tighter tip band
STOCK_NEAR_ENTRY_UP = 0.015  # ±1.5% symmetric entry buffer band
ETF_NEAR_ENTRY_DOWN = 0.010
STOCK_NEAR_ENTRY_DOWN = 0.015
# Hero action must not re-upgrade to 可买入 when these algo_notes fired.
BUY_DEMOTE_LOCK_NOTES = (
    "恐慌禁开仓",
    "高潮降级",
    "指数闸门",
    "风格闸门",
    "缩量闸门",
    "负溢价闸门",
    "大面闸门",
    # similar-day cool is size-only now (not an action demote lock)
    "开盘静音",
    "极端拥挤禁开",
    "竞价观望",
    "竞价弱开闸门",
    "竞价强开防追",
    "竞价开盘桥",
)
# Minute-structure tip / shallow pullback (percent of recent minute high).
MINUTE_TIP_THR_NARROW = 0.25
MINUTE_TIP_THR_WIDE = 0.35
MINUTE_GRIND_PULLBACK = 0.55
MINUTE_SHALLOW = 0.28
MINUTE_VOL_PULLBACK = 0.55
# Tip / shallow labels still block full ready, but allow half-size probe.
TIP_PROBE_ALLOW_FAILS = frozenset(
    {
        "分时贴近近期高点",
        "分时仍在抬高点",
        "分时回撤过浅",
    }
)
# After sticky mainline switches: protect new-theme strong bags this long.
SWITCH_SELL_GRACE_SECONDS = 45 * 60  # 45 minutes (within 30–60 band)
# Strong auction + weak open (first N minutes after 09:30) → demote / revoke probe.
AUCTION_OPEN_BRIDGE_MINUTES = 15
AUCTION_OPEN_STRONG_MEDIAN = 2.0
AUCTION_OPEN_WEAK_HS300 = -0.3

# Opening Auction Alpha: 09:20–09:25 non-cancellable tape per candidate code.
AUCTION_ALPHA_TAPE_START = 920          # HHMM; first sample kept (cancel window closed)
AUCTION_ALPHA_TAPE_END = 926            # HHMM; stop sampling after the 09:25 print settles
AUCTION_ALPHA_MIN_SAMPLES = 3           # fewer tape points → no verdict (soft unknown)
AUCTION_ALPHA_CANDIDATE_CAP = 40        # codes sampled per auction refresh
AUCTION_ALPHA_GAP_MIN = 1.0             # % open needed before a "trap" can fire
AUCTION_ALPHA_FADE_PCT = 1.2            # pp give-back from the 09:20+ tape peak → fade
AUCTION_ALPHA_SLOPE_FADE = -0.20        # pp/min regression slope → fading grab
AUCTION_ALPHA_YZT_EXPECT = 2.0          # % open expected after yesterday's limit-up
AUCTION_ALPHA_MISS_PP = 2.0             # open below expectation by this → "不及预期"
AUCTION_ALPHA_BEAT_PP = 2.5             # open above peer baseline by this → beat
AUCTION_ALPHA_PEER_WEAK = 0.5           # peer median ≤ this → sector divergent/weak
AUCTION_ALPHA_PEER_SPREAD = 2.0         # peer open stdev ≥ this → divergent
AUCTION_ALPHA_VOL_RATIO_MIN = 1.5       # 量比 at 09:25 confirming a real grab
AUCTION_ALPHA_PEER_MIN = 3              # group needs this many sampled peers
AUCTION_ALPHA_TRAP_SIZE_MULT = 0.7      # trap after the open window: soft shrink
AUCTION_ALPHA_TRAP_SOFT_UNTIL = 1030    # HHMM; trap soft shrink stops after this

# Volume Absorption at the buy band (minute tick-rule + outer/inner volume).
ABSORB_LOOKBACK = 20                    # minute bars for the tick-rule buy share
ABSORB_MIN_BARS = 10                    # fewer bars with volume → unknown (soft)
ABSORB_BUY_SHARE_OK = 0.50              # tick-rule active-buy share that confirms
ABSORB_BUY_SHARE_FAIL = 0.42            # below this with a falling VWAP → no absorption
ABSORB_OUTER_FAIL = 0.42                # day outer/(outer+inner) below this backs a fail
ABSORB_VWAP_SLOPE_FAIL = -0.015         # %/min VWAP slope considered "rolling over"
ABSORB_VWAP_BARS = 15                   # bars spanned by the VWAP slope

# Dynamic slippage / impact cost from the five-level book.
SLIP_SPREAD_WARN_BPS = 25.0             # (ask1-bid1)/mid
SLIP_SPREAD_THIN_BPS = 60.0
SLIP_IMPACT_WARN_BPS = 20.0             # walked-book avg fill vs mid
SLIP_IMPACT_THIN_BPS = 50.0
SLIP_DEPTH_TAKE_MAX = 0.5               # planned qty ≤ this share of 5-level ask depth
SLIP_DENSITY_MIN_STOCK = 3e5            # 元 per session minute; below → thin warn
SLIP_DENSITY_MIN_ETF = 2e5
SLIP_WARN_SIZE_MULT = 0.75
SLIP_THIN_SIZE_MULT = 0.5

# Crowding Index: board turnover / whole-market turnover (沪+深 index amounts).
CROWD_ABS_EXTREME_PCT = 12.0            # industry share ≥ this → extreme (hard no-new-open)
CROWD_ABS_WARN_PCT = 9.0                # industry share ≥ this → warn (soft size)
CROWD_REL_EXTREME_MULT = 2.0            # share ≥ 20d median × this AND ≥ 60d peak → extreme
CROWD_REL_WARN_MULT = 1.6               # share ≥ 20d median × this → warn
CROWD_REL_FLOOR_INDUSTRY = 5.0          # relative rules need at least this share (%)
CROWD_REL_FLOOR_CONCEPT = 8.0           # concepts overlap heavily → higher floor, relative only
CROWD_HIST_MIN_DAYS = 10                # relative rules need this many history days
CROWD_MIN_SESSION_MIN = 15              # skip the noisy first minutes after 09:30
CROWD_MIN_MARKET_YI = 300.0             # market turnover floor (亿) before judging
CROWD_WARN_SIZE_MULT = 0.8
CROWD_SAVE_EVERY_SEC = 300              # intraday history upsert cadence
CROWD_BAN_FLAG = "极端拥挤·高潮禁开新仓"
# East Money industry boards are a 3-level SW-style tree; level-1 parents (电子 ≈ 25%)
# naturally exceed the absolute bands, so they are judged against their own history only.
CROWD_ABS_EXEMPT = frozenset({
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器", "食品饮料",
    "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输", "房地产", "商贸零售",
    "社会服务", "银行", "非银金融", "综合", "建筑材料", "建筑装饰", "电力设备",
    "机械设备", "国防军工", "计算机", "传媒", "通信", "煤炭", "石油石化", "环保", "美容护理",
})
# Extreme alone → strong soft; extreme + any confirmation below → hard ban.
CROWD_EXTREME_SOFT_MULT = 0.5           # fully confirmed ready keeps half size
CROWD_SOFT_FLAG = "极端拥挤·半仓"
CROWD_CONFIRM_FLAGS = ("滞涨", "A杀", "天地", "退潮")  # board cycle flags that confirm a climax / ebb
CROWD_CONFIRM_ZB_MIN = 2                # broken limit-ups needed …
CROWD_CONFIRM_ZB_RATIO = 0.4            # … and their share of touched limit-ups

# Broad-ETF liquidity pulse (300 / 500 / 1000 / 50 / 科创 / 创业板 core ETFs).
BROAD_ETF_PULSE = [
    ("510300", "沪深300ETF"),
    ("510500", "中证500ETF"),
    ("512100", "中证1000ETF"),
    ("510050", "上证50ETF"),
    ("588000", "科创50ETF"),
    ("159915", "创业板ETF"),
]
ETF_PULSE_WINDOW = 3                    # minutes summed per pulse window
ETF_PULSE_VOL_RATIO = 4.0               # window volume vs same-minute baseline median
ETF_PULSE_PX_MIN = 0.3                  # % price lift across the window
ETF_PULSE_WEAK_INDEX = -0.8             # 沪深300 pct at/below → weak tape
ETF_PULSE_DIP_PCT = 1.0                 # or ETF sat ≥ this % below prev close before the lift
ETF_PULSE_MIN_COUNT = 2                 # rescue pulses within the lookback → market event
ETF_PULSE_LOOKBACK_MIN = 10
ETF_PULSE_FETCH_SEC = 60                # minute refetch cadence per ETF

# Narrative graph (shadow mode): cross-industry clusters of limit-up names.
NARR_EXTRA_CONCEPTS = 20                # extra top-pct concept boards whose members are fetched
NARR_FETCH_EVERY_SEC = 300
NARR_MIN_SHARED = 2                     # limit-ups a concept needs to join the graph
NARR_EDGE_JACCARD = 0.3                 # concept-concept edge threshold
NARR_MIN_ZT = 4                         # cluster limit-ups to be a candidate
NARR_MIN_INDUSTRIES = 2                 # distinct industries → cross-industry narrative

# Ready / structure gates (centralized; was scattered magic numbers).
ETF_BOUNCE_BUY_MIN = 0.35          # carrier rebound from day low to allow 可买入
STOCK_WEAK_VS_ETF_PCT = 1.5        # stock pct must not lag mapped ETF by more than this
ETF_THIN_AMOUNT = 8e7              # yuan; green ETF below this → volume fail
# ~15 one-minute bars after 09:30; was 25 and kept gating soft until ~09:55.
MINUTE_SAMPLE_MIN = 15

# Fund-flow soft feed into mainline score / sell urgency (亿元).
MAINLINE_FLOW_IN_YI = 0.8
MAINLINE_FLOW_OUT_YI = -1.0
MAINLINE_FLOW_IN_ADJ = 4.0
MAINLINE_FLOW_OUT_ADJ = -5.0
MAINLINE_FLOW_STICKY_BONUS = 2.5  # in day+5d sticky inflow leaders
SELL_FLOW_OUT_YI = -1.5  # theme outflow → tighten sell soft floor slightly

# Soft / orphan stock listing (no exact ETF map).
SOFT_STOCK_PB_MIN = 1.1
SOFT_STOCK_MV_MULT = 1.12

# Stock vs board strength / flow quality.
STOCK_VS_BOARD_WEAK_PCT = 1.2
STOCK_FLOW_OUT_SCORE_PEN = 6.0

# Buy-gate auto-loosen when review false-kills are high.
BUY_GATE_FALSE_KILL_MIN = 3
BUY_GATE_KILL_MIN = 5
BUY_GATE_LOOSEN_MULT = 0.88
BUY_GATE_TRUE_KILL_MIN = 4
BUY_GATE_TIGHTEN_MULT = 1.12

# Prefer boards with a clear low-height leader + followers (not tip height).
MAINLINE_LEADER_STRUCT_BONUS = 4.0   # 1–2板龙 + 跟风结构
MAINLINE_LEADER_PAIR_BONUS = 2.0     # extra when 二板龙 + 卡位/跟风
MAINLINE_LEADER_MAINBOARD_BONUS = 1.5
# Breadth: absolute limit-up count with soft diminishing returns (20 still ≫ 4).
MAINLINE_ZT_UNIT = 6.0              # points per zt in the first band
MAINLINE_ZT_SOFT_START = 12         # beyond this, marginal zt is cheaper
MAINLINE_ZT_SOFT_UNIT = 3.0
MAINLINE_ZT_TAIL_START = 18         # beyond this, tiny tail credits only
MAINLINE_ZT_TAIL_UNIT = 1.0
# Thin niches: damp so 金属钨(4) cannot beat 计算机(20) on status alone.
MAINLINE_THIN_ZT_MAX = 4
MAINLINE_THIN_PEN_PER = 5.0         # (MAX+1 - zt_n) * this when zt ≤ MAX

# Stock recommend: cross-board membership resonance (cap keeps pullback quality first).
STOCK_CROSS_BOARD_BASE = 2.0       # per extra hot board beyond the scoring board
STOCK_CROSS_THEME_BONUS = 3.0      # extra when that board shares mainline theme
STOCK_CROSS_CONFIRM_BONUS = 2.0    # extra when that board status is 确认中
STOCK_CROSS_BOARD_CAP = 12.0

# Daily trend score nudge after kline classify (unclear / missing → 0).
STOCK_TREND_UP_BONUS = 12.0
STOCK_TREND_DOWN_PENALTY = 12.0
# Soft size scale after trend classify (lot-rounded qty on buy cards).
TREND_SIZE_UP_MULT = 1.10
TREND_SIZE_DOWN_MULT = 0.75
TREND_SIZE_SIDEWAYS_MULT = 0.85  # stock sideways: soft shrink, no ready kill
STOCK_TREND_SIDEWAYS_PENALTY = 6.0  # softer than full down penalty
# Minute sample thin: keep ready, soft shrink (structure fail still hard-gates).
MINUTE_PENDING_SIZE_MULT = 0.85
# Mainline near-entry probe when hard gates pass but full ready not armed.
PROBE_SIZE_MULT = 0.50
# Mainline carrier ETF daily-trend nudge (same once-per-day closes).
MAINLINE_ETF_TREND_UP = 9.0
MAINLINE_ETF_TREND_DOWN = 9.0
# Soft-mapped carrier: smaller adj only; never unlocks ready buys.
MAINLINE_SOFT_ETF_TREND_UP = 3.5
MAINLINE_SOFT_ETF_TREND_DOWN = 4.0
# Ending / 退潮 incumbent: easier for challenger to take sticky mainline.
MAINLINE_FADE_SWITCH_MULT = 0.55

# Sell-review feedback: adjust pb/pocket from historical sell outcomes.
SELL_REVIEW_MIN_N = 10
SELL_REVIEW_KIND_MIN_N = 6       # etf/stock split can fire earlier
SELL_REVIEW_WIDEN_BELOW = 40.0   # 卖后回落命中偏低 → 卖早 → 放宽
SELL_REVIEW_TIGHTEN_ABOVE = 60.0  # 卖后回落命中偏高 → 略收紧止盈回撤
SELL_REVIEW_WIDEN_MULT = 1.12
SELL_REVIEW_TIGHTEN_MULT = 0.96  # was 0.92; less aggressive auto-tighten
SELL_SIM_URGENT_FLOOR = 0.85     # floor when similar urgent stacks on review tighten
SELL_SIM_URGENT_FLOOR = 0.85     # floor when similar urgent stacks on review tighten

# Soft trim floors — avoid cutting winners that still have room.
SELL_SOFT_MIN_PNL_STOCK = 2.0
SELL_SOFT_MIN_PNL_ETF = 1.5
SELL_SOFT_MIN_PNL_ENDING_STOCK = 1.0
SELL_SOFT_MIN_PNL_ENDING_ETF = 0.8
SELL_SOFT_CLIMAX_MIN_PNL_STOCK = 3.0  # climax alone needs more profit
SELL_SOFT_CLIMAX_MIN_PNL_ETF = 2.0
SELL_SOFT_DEEP_PNL_STOCK = 4.0
SELL_SOFT_DEEP_PNL_ETF = 2.5
SELL_CARRIER_FALL_PCT = 0.35  # carrier must drop ≥ this % vs prior tick
SELL_ENDING_DEFENSE_PNL = 0.0  # ending flat trim only at ≤0% (not +0.2%)
# Panic soft-trim: skip when the name itself is still green / relatively strong.
SELL_PANIC_REL_STRONG_PCT = 0.5  # day change ≥ this → no panic soft-exit
SELL_REL_STRONG_PCT = 0.5  # general: green enough to hold through soft/light takes
SELL_VS_INDEX_EDGE = 1.5  # or beat HS300 by this many pct pts
SELL_TIP_HOLD_PB = 0.45  # day-high pullback below this → still extending, don't soft/light-take
SELL_DAY_WEAK_PCT = -2.5  # day dump → earlier soft floor
SELL_DAY_WEAK_SOFT_DELTA = -0.8  # add to soft_min (negative = easier trim)
# Daily uptrend: hold winners longer (widen bands + raise soft-trim floor).
SELL_UPTREND_BAND_MULT = 1.18
SELL_SOFT_UPTREND_EXTRA = 1.5  # add to soft-trim min pnl when daily up
# Sell-side multi-theme: include hot boards within this score gap of sticky.
# Buys still follow sticky only; sells may match primary / side / these peers.
SELL_THEME_GAP = 12.0
SELL_THEME_MAX = 5

# Orphan mainline (no exact ETF): stricter stock pullback / cap filters.
ORPHAN_STOCK_PB_MIN = 1.2
ORPHAN_STOCK_MV_MULT = 1.25
# Thin「确认中」(zt<=2): stock ready needs cross-board OR same-theme resonance.
THIN_CONFIRM_ZT_MAX = 2
THIN_CROSS_BOARD_MIN = 2
THIN_CROSS_THEME_MIN = 1

# Sell-band regimes: widen on strong mainline+uptrend, tighten on fade/down.
# Multipliers are vs cost: stop_buy=0.97 → −3% from cost.
SELL_BAND_STOCK = {
    "neutral": {
        "stop_buy": 0.97,
        "stop_floor": 0.96,
        "pnl_stop": -3.0,
        "pb_light": 1.5,
        "pb_deep": 2.5,
        "take_pnl": 5.0,
        "take_deep_pnl": 6.0,
        "pocket_pnl": 8.0,
    },
    "give": {
        "stop_buy": 0.96,
        "stop_floor": 0.955,
        "pnl_stop": -4.0,
        "pb_light": 2.0,
        "pb_deep": 3.2,
        "take_pnl": 6.0,
        "take_deep_pnl": 7.5,
        "pocket_pnl": 10.0,
    },
    "tight": {
        "stop_buy": 0.98,
        "stop_floor": 0.97,
        "pnl_stop": -2.0,
        "pb_light": 1.3,
        "pb_deep": 2.2,
        "take_pnl": 4.5,
        "take_deep_pnl": 5.5,
        "pocket_pnl": 7.0,
    },
}
SELL_BAND_ETF = {
    "neutral": {
        "stop_buy": 0.985,
        "stop_floor": 0.98,
        "pnl_stop": -1.5,
        "pb_light": 0.8,
        "pb_deep": 1.2,
        "take_pnl": 2.5,
        "take_deep_pnl": 3.5,
        "pocket_pnl": 4.0,
    },
    "give": {
        "stop_buy": 0.98,
        "stop_floor": 0.975,
        "pnl_stop": -2.0,
        "pb_light": 1.1,
        "pb_deep": 1.6,
        "take_pnl": 3.0,
        "take_deep_pnl": 4.0,
        "pocket_pnl": 5.0,
    },
    "tight": {
        "stop_buy": 0.99,
        "stop_floor": 0.985,
        "pnl_stop": -1.0,
        "pb_light": 0.75,
        "pb_deep": 1.1,
        "take_pnl": 2.2,
        "take_deep_pnl": 3.0,
        "pocket_pnl": 3.5,
    },
}

# Phase classification temperature thresholds (overridable via settings).
PHASE_PANIC_TEMP = 28
PHASE_FERMENT_TEMP = 45
PHASE_CLIMAX_TEMP = 72

# Gap-and-fade blacklist: high open then fade from open.
GAP_FADE_OPEN_PCT = 2.5          # open vs prev close
GAP_FADE_DROP_PCT = 1.5          # last below open by at least this %
# Bought yesterday (sellable window) + weak session: prefer half, raise clear bar.
SELL_YDAY_BUY_MAX_AGE_DAYS = 3   # calendar days after buy still「昨买窗」(covers weekend)
SELL_YDAY_BUY_WEAK_PCT = -2.0    # day change ≤ this →「今弱」(−1% too noisy)
SELL_YDAY_BUY_CLEAR_EXTRA = -1.5 # hard clear only if pnl ≤ pnl_stop + this
# Independent popular pullback positions: skip「昨买今弱」half trim.
SELL_INDEPENDENT_POP_EXEMPT_YDAY = True
# After half-trim: don't escalate soft/take clear while still strong (regret window).
SELL_REGRET_ENABLED = True
# 昨买今弱 repair: day pct recovered above this → cancel weak trim.
SELL_YDAY_RECOVER_PCT = -0.5
# Soft floor nudge when lagging carrier (not at tip).
SELL_CARRIER_WEAK_SOFT_DELTA = -0.6
# Elliott soft sell: mid wave-3 hold longer; wave-5 / C end slightly easier trim.
SELL_WAVE_W3_SOFT_EXTRA = 1.2
SELL_WAVE_END_SOFT_DELTA = -0.5
# Review「卖飞」: left-on-table after sell.
SELL_FLY_MAE_PCT = 3.0
SELL_FLY_DAY1_PCT = -2.5
# Ready style: strict = full gates; band = near_entry & below chase → soft ready (half size).
READY_STYLE_DEFAULT = "band"
SELL_MINUTE_GATE_ENABLED = True

# Dual-dragon + independent popular pullback (observe-only; soft filters).
INDEPENDENT_POP_MAX = 5
INDEPENDENT_POP_LOW_DAYS = 5
INDEPENDENT_POP_NEAR_LOW_PCT = 3.5       # engine 5-day low band (was 2.0; too empty)
INDEPENDENT_POP_NEAR_DAY_LOW_PCT = 3.5   # session low pre-filter
INDEPENDENT_POP_PCT_MAX = 7.0            # soft window max pct (+7.0% upper bound)
INDEPENDENT_POP_PCT_MIN = -3.0           # soft window min pct (-3.0% lower bound)
INDEPENDENT_POP_MIN_AMOUNT = 2.5e8       # 2.5 亿元流动性底线 (优先成交活跃核心)
INDEPENDENT_POP_KEEP_DAY_LOW_IF_5D_MISS = True  # soft: keep day-low when 5d fails
# Absolute floor + turnover floors by market-cap bucket (亿元 / %).
STOCK_MV_HARD_MIN_YI = 100.0          # below → always drop
STOCK_TURN_MIN_MID = 3.0              # 100–500亿: turnover < this → drop
STOCK_TURN_MIN_LARGE = 2.0            # >500亿: turnover < this → drop
STOCK_MV_MID_MAX_YI = 500.0
# Theme reputation / board affinity (soft mainline score feed).
THEME_FADE_ZT_DROP = 0.35       # next-day zt ≤ prior * this → fade (stricter)
THEME_FADE_PCT_MAX = 0.3        # weak pct with thin zt (stricter)
THEME_PERSIST_ZT_MIN = 2
THEME_PERSIST_PCT_MIN = 1.5
THEME_REP_MIN_SAMPLES = 3       # label / extreme tip need this many graded days
THEME_REP_THIN_N = 3            # below → shrink feed into mainline
THEME_REP_THIN_FEED_MULT = 0.2  # mainline rep_adj scale when thin
THEME_REP_SETTLE_MAX = 10       # max themes graded per day (was 6)
THEME_REP_SETTLE_ZT_MIN = 2     # prior heat floor for secondary themes
THEME_REP_SETTLE_PCT_MIN = 1.5
THEME_REP_ADJ_MIN = -12.0
THEME_REP_ADJ_MAX = 8.0         # room for sticky persist bonus
# Bump when compute_rep_adj / outcome weights / settle rules change.
# v4: relative to the market-wide persist base rate, holiday / pseudo-board rows dropped.
THEME_REP_FORMULA_VERSION = 4
THEME_REP_BASE_WINDOW = 80      # recent graded outcomes (all themes) for the base persist rate
THEME_REP_BASE_DEFAULT = 0.30   # base rate when the window is thin
THEME_REP_BASE_MIN_N = 20
THEME_REP_PRIOR_N = 4.0         # pseudo-samples pulling a theme toward the base rate
THEME_REP_REL_SCALE = 25.0      # (shrunk theme rate − base) * scale → auto_adj
# Style / flow / index buckets are not themes: no reputation, no mainline pick.
PSEUDO_BOARD_KEYWORDS: tuple[str, ...] = (
    "热股", "题材股", "QFII", "重仓", "百元股", "低价股", "破发", "破净", "次新",
    "最近多板", "昨日", "连板", "首板", "涨停", "融资融券", "沪股通", "深股通",
    "MSCI", "标普", "富时", "券商金股", "反转股", "预盈", "预增", "预亏", "送转",
    "转债标的", "壳资源", "AH股", "B股", "HS300", "上证50", "上证180", "上证380",
    "中证500", "深成500", "创业板综", "茅指数", "宁组合", "微盘", "机构重仓", "证金",
)
THEME_SIM_PEER_MIN = 0.45       # show peers above this
# Soft board-linkage buys: similar peer cards when mainline has no ready / overheated.
BOARD_LINK_SIM_MIN = 0.45       # reuse peer floor; raise to be stricter
BOARD_LINK_SIZE_MULT = 0.75     # extra size damp vs mainline risk plan
BOARD_LINK_MAX_STOCKS = 2
BOARD_LINK_CHASE_RATIO = 0.997  # last ≥ chase * this → counts as hug-chase
# Soft link when similar_peers is empty: same-theme / score-near runners.
BOARD_LINK_THEME_FALLBACK_SIM = 0.52
BOARD_LINK_SCORE_FALLBACK_SIM = 0.48
# Watchlist「可试探」desk side cards (same soft tier as board link).
WATCH_TRIAL_SIZE_MULT = 0.75
WATCH_TRIAL_MAX_ITEMS = 4
# Soft damp side-card size when review hit-rate lags mainline (desk_source buckets).
DESK_SRC_HIT_MIN_N = 5
DESK_SRC_HIT_GAP_PP = 15.0      # link/trial hit_rate ≤ main − this → damp
DESK_SRC_HIT_DAMP = 0.85        # multiply BOARD_LINK / WATCH_TRIAL size
# Smarter affinity: weight strong members / leaders / board co-move.
THEME_MEMBER_SIM_WEIGHT = 0.40  # unweighted Jaccard share (fallback mix)
THEME_WEIGHTED_MEMBER_WEIGHT = 0.35  # pct/leader-weighted Jaccard
THEME_TOP_OVERLAP_WEIGHT = 0.20  # top movers code overlap
THEME_LEADER_CROSS_BONUS = 0.12  # leader of A in B (or same leader)
THEME_COMOVE_BONUS = 0.10        # same-direction board % move
THEME_TOP_K = 12                 # top movers by |pct| for overlap
THEME_SIM_INHERIT = 0.35        # fraction of peer bad-rep inherited
THEME_SIM_INHERIT_MIN = 0.70    # only inherit from strong peers
THEME_SIM_POS_INHERIT = 0.22    # milder positive inheritance
THEME_SIM_POS_INHERIT_MIN = 0.80
THEME_REP_DECAY = 0.85          # per older outcome when refreshing adj
THEME_REP_EARLY_MULT = 0.35     # legacy; compute_rep_adj now uses conf curve
THEME_REP_STREAK_BONUS = 1.5    # |bonus| for 2+ same-side streak (persist or fade)
THEME_REP_RATE_SCALE = 7.5      # (persist_rate - fade_rate) * scale → primary adj
THEME_REP_CONF_DENOM = 4.5      # conf = min(1, sample_w / denom); softens thin samples
THEME_REP_EXTREME_RATE = 0.65   # lopsided habit bonus/penalty kicks in above this
THEME_REP_NEWEST_TIP = 0.55     # extra nudge from the most recent outcome
THEME_MANUAL_ADJ_MIN = -8.0
THEME_MANUAL_ADJ_MAX = 8.0

# Adaptive soft feedback (size heat / segment sell / MAE sweet / stock rep).
ADAPT_HEAT_RECENT_N = 20
ADAPT_HEAT_MIN_N = 5
ADAPT_CONSEC_LOSS_SOFT = 3
ADAPT_CONSEC_LOSS_HARD = 5
ADAPT_LOSS_MULT_SOFT = 0.5
ADAPT_LOSS_MULT_HARD = 0.25
ADAPT_WIN_RATE_MIN = 55.0
ADAPT_WIN_PF_MIN = 1.4
ADAPT_WIN_MULT_CAP = 1.35
ADAPT_SIZE_MULT_MIN = 0.2
ADAPT_SIZE_MULT_MAX = 1.5
# Final product clamp for stacked size factors (meta controller).
ADAPT_META_SIZE_MIN = 0.35
ADAPT_META_SIZE_MAX = 1.35
ADAPT_PHASE_KIND_MIN_N = 5
ADAPT_TUNE_CLAMP = 0.20          # ±20% auto-tune envelope (single shared clamp)
ADAPT_MISSED_MIN_N = 3           # same context-bucket misses before nudge
ADAPT_CTX_GATE_MIN_N = 4         # per-context gate kills before threshold nudge
ADAPT_VOL_HS300_ABS = 1.2        # |沪深300%| ≥ → high vol
ADAPT_VOL_AMOUNT_PCTILE = 70     # amount percentile ≥ → high vol
ADAPT_PULLBACK_CLAMP = 0.20
ADAPT_STOCK_REP_ADJ_MIN = -10.0
ADAPT_STOCK_REP_ADJ_MAX = 6.0
ADAPT_STOCK_WATCH_STREAK = 2
ADAPT_EXEC_MIN_N = 3             # traded fills before exec soft-size fires
ADAPT_EXEC_SCORE_SOFT = 70.0     # below → mild shrink
ADAPT_EXEC_SCORE_HARD = 50.0     # below → stronger shrink
ADAPT_EXEC_CHASE_RATIO = 0.40    # chase share ≥ → shrink
THEME_TRADE_ADJ_MIN = -6.0
THEME_TRADE_ADJ_MAX = 4.0
# Session segment → sell-band soft mult (prior; learned rates override when n enough).
SEGMENT_SELL_MULT = {
    "auction": 1.12,
    "open30": 1.10,
    "morning": 1.0,
    "afternoon": 0.88,
    "closed": 1.0,
}
SEGMENT_SELL_LEARN_MIN_N = 6  # per-segment sample to replace static prior
# Sell MFE: left-on-table from early sells → widen/tighten take-profit soft.
SELL_MFE_MIN_N = 6
SELL_MFE_WIDEN_ABOVE = 3.0   # median |MAE| of 卖后继续涨 (%) → widen take
SELL_MFE_TIGHTEN_BELOW = 1.2
SELL_MFE_WIDEN_MULT = 1.10
SELL_MFE_TIGHTEN_MULT = 0.94

# White-box logistic feature weights (soft score only).
WHITEBOX_MIN_N = 12
WHITEBOX_L2 = 0.35
WHITEBOX_LR = 0.35
WHITEBOX_MAX_ITERS = 250
WHITEBOX_WEEK_SPLIT = 0.45   # newer fraction reserved for "current" vs older fit
WHITEBOX_SCORE_SCALE = 4.0   # raw contribution → soft mainline/candidate points

# ---------------------------------------------------------------------------
# Stage 1: Defense & Exit Reinforcement
# ---------------------------------------------------------------------------
# 1. Break-even Defense Shield
SELL_BREAKEVEN_TRIGGER_PNL = 2.5       # Hold peak gain (%) to arm break-even shield
SELL_BREAKEVEN_BUFFER_PCT = 0.3        # Cushion above cost price (covers fee & slip)
SELL_BREAKEVEN_ETF_TRIGGER_PNL = 1.5   # Trigger gain for ETF

# 2. Sector De-sync & Crack Alert
SELL_SECTOR_CRACK_ENABLED = True
SELL_SECTOR_CRACK_DRAGON_BLOW_DROP = -2.5   # Dragon failed limit-up or dropped from peak
SELL_SECTOR_CRACK_DIVERGENT_DOWN_N = 2      # >= N members diving hard
SELL_SECTOR_CRACK_DIVERGENT_DOWN_PCT = -6.0 # Member diving threshold (%)

# 3. Trailing ATR Adaptive Exit
SELL_ATR_EXIT_ENABLED = True
SELL_ATR_HIGH_BETA_AMP = 6.0           # Daily amplitude >= 6% -> high beta leader
SELL_ATR_LOW_BETA_AMP = 2.5            # Daily amplitude <= 2.5% -> low beta / broad ETF
SELL_ATR_HIGH_BETA_MULT = 1.25         # Widen stop and pullback thresholds
SELL_ATR_LOW_BETA_MULT = 0.82          # Tighten stop and pullback thresholds

