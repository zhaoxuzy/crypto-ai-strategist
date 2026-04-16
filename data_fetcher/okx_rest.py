import requests
from utils.logger import logger

def get_current_price(symbol: str = "BTC-USDT-SWAP") -> float:
    url = "https://www.okx.com/api/v5/market/ticker"
    params = {"instId": symbol}
    try:
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json()
        if data.get("code") == "0":
            return float(data["data"][0]["last"])
        else:
            logger.error(f"OKX 价格获取失败: {data.get('msg')}")
            return 0.0
    except Exception as e:
        logger.error(f"OKX 请求异常: {e}")
        return 0.0

def get_klines(symbol: str = "BTC-USDT-SWAP", bar: str = "1H", limit: int = 60) -> list:
    """获取 K 线数据，返回原始列表"""
    url = "https://www.okx.com/api/v5/market/candles"
    params = {"instId": symbol, "bar": bar, "limit": str(limit)}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("code") == "0":
            return data["data"]
        else:
            logger.error(f"OKX K线获取失败: {data.get('msg')}")
            return []
    except Exception as e:
        logger.error(f"OKX K线请求异常: {e}")
        return []

def calculate_atr(symbol: str = "BTC-USDT-SWAP", period: int = 14) -> float:
    klines = get_klines(symbol, "1H", period + 1)
    if len(klines) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if true_ranges:
        atr = sum(true_ranges[-period:]) / period
        return round(atr, 2)
    return 0.0

def calculate_ema(klines: list, period: int = 55) -> float:
    """从K线数据计算 EMA 值（默认55周期）"""
    if not klines or len(klines) < period:
        return 0.0

    closes = []
    for k in klines:
        if len(k) >= 5:
            closes.append(float(k[4]))

    if len(closes) < period:
        return 0.0

    # 取最近 period 根K线
    closes = closes[-period:]
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period  # 初始SMA

    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema

    return round(ema, 2)
