"""Runtime configuration for the market desk."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "desk.db"
STATIC_DIR = Path(__file__).resolve().parent / "static"

HOST = "127.0.0.1"
PORT = 8765
# Hit East Money / Tencent only in session; idle loop just waits for the next open.
SESSION_REFRESH_SECONDS = 90
IDLE_CHECK_SECONDS = 60

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
    ("sz159915", "159915", "创业板ETF"),
    ("sh588000", "588000", "科创50ETF"),
]

# These two ETFs are tradable. ChiNext / STAR stocks are not recommended.
CHINEXT_STAR_ETFS = frozenset({"159915", "588000"})

MAINLINE_ETF_RULES: list[tuple[tuple[str, ...], tuple[str, str, str]]] = [
    (("通信",), ("sh515050", "515050", "通信ETF")),
    (("半导体", "芯片", "集成电路"), ("sh512480", "512480", "半导体ETF")),
    (("人工智能", "算力", "光模块", "液冷", "服务器", "光学光电子"), ("sz159819", "159819", "人工智能ETF")),
    (("医药", "制药", "医疗", "中药", "生物"), ("sh512010", "512010", "医药ETF")),
    (("证券", "券商"), ("sh512880", "512880", "证券ETF")),
    (("军工", "航天", "航空"), ("sh512660", "512660", "军工ETF")),
    (("新能源", "光伏", "锂电", "电池"), ("sh516160", "516160", "新能源ETF")),
    (("有色", "稀土", "黄金"), ("sh512400", "512400", "有色ETF")),
    (("传媒", "游戏", "影视", "广告"), ("sh512980", "512980", "传媒ETF")),
    (("银行",), ("sh512800", "512800", "银行ETF")),
    (("煤炭",), ("sh515220", "515220", "煤炭ETF")),
    (("房地产", "地产"), ("sh512200", "512200", "房地产ETF")),
    (("创新药", "港股"), ("sh513120", "513120", "港股创新药ETF")),
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
