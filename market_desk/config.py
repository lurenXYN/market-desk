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

# Soft risk hints for the local position book (not hard blocks).
POSITION_MAX_NAMES = 6
POSITION_MAX_SINGLE_PCT = 35.0
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
# Wait price as a fraction of last (shallower wait → nearer entry tags).
ETF_WAIT_GAP = 0.9955   # ~0.45% below last
STOCK_WAIT_GAP = 0.9915  # ~0.85% below last
# Relative band around suggested buy for「回踩到位」.
ETF_NEAR_ENTRY_UP = 0.0045   # was 0.55%; tighter tip band
STOCK_NEAR_ENTRY_UP = 0.0055  # was 0.85%
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
    "竞价观望",
    "竞价弱开闸门",
    "竞价强开防追",
)
# Minute-structure tip / shallow pullback (percent of recent minute high).
MINUTE_TIP_THR_NARROW = 0.25
MINUTE_TIP_THR_WIDE = 0.35
MINUTE_GRIND_PULLBACK = 0.55
MINUTE_SHALLOW = 0.28
MINUTE_VOL_PULLBACK = 0.55

# Ready / structure gates (centralized; was scattered magic numbers).
ETF_BOUNCE_BUY_MIN = 0.35          # carrier rebound from day low to allow 可买入
STOCK_WEAK_VS_ETF_PCT = 1.5        # stock pct must not lag mapped ETF by more than this
ETF_THIN_AMOUNT = 8e7              # yuan; green ETF below this → volume fail
MINUTE_SAMPLE_MIN = 25

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

# Stock recommend: cross-board membership resonance (cap keeps pullback quality first).
STOCK_CROSS_BOARD_BASE = 2.0       # per extra hot board beyond the scoring board
STOCK_CROSS_THEME_BONUS = 3.0      # extra when that board shares mainline theme
STOCK_CROSS_CONFIRM_BONUS = 2.0    # extra when that board status is 确认中
STOCK_CROSS_BOARD_CAP = 12.0

# Daily trend score nudge after kline classify (unclear / missing → 0).
STOCK_TREND_UP_BONUS = 8.0
STOCK_TREND_DOWN_PENALTY = 8.0
# Mainline carrier ETF daily-trend nudge (same once-per-day closes).
MAINLINE_ETF_TREND_UP = 6.0
MAINLINE_ETF_TREND_DOWN = 6.0
# Soft-mapped carrier: smaller adj only; never unlocks ready buys.
MAINLINE_SOFT_ETF_TREND_UP = 2.5
MAINLINE_SOFT_ETF_TREND_DOWN = 3.0
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
# Theme reputation / board affinity (soft mainline score feed).
THEME_FADE_ZT_DROP = 0.45       # next-day zt ≤ prior * this → fade candidate
THEME_FADE_PCT_MAX = 0.5        # and/or weak pct with thin zt
THEME_PERSIST_ZT_MIN = 2
THEME_PERSIST_PCT_MIN = 1.5
THEME_REP_MIN_SAMPLES = 2       # mild adj before this; full after
THEME_REP_ADJ_MIN = -12.0
THEME_REP_ADJ_MAX = 8.0         # room for sticky persist bonus
THEME_SIM_PEER_MIN = 0.45       # show peers above this
THEME_SIM_INHERIT = 0.35        # fraction of peer bad-rep inherited
THEME_SIM_INHERIT_MIN = 0.70    # only inherit from strong peers
THEME_SIM_POS_INHERIT = 0.22    # milder positive inheritance
THEME_SIM_POS_INHERIT_MIN = 0.80
THEME_MEMBER_SIM_WEIGHT = 0.55  # Jaccard share in affinity
THEME_REP_DECAY = 0.85          # per older outcome when refreshing adj
THEME_REP_EARLY_MULT = 0.25     # scale adj before MIN_SAMPLES
THEME_REP_STREAK_BONUS = 1.8    # extra when newest 2+ outcomes are persist
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
