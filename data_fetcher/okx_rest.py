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

def calculate_atr(symbol: str = "BTC-USDT-SWAP", period: int = 14) -> float:
    url = "https://www.okx.com/api/v5/market/candles"
    params = {"instId": symbol, "bar": "1H", "limit": str(period + 1)}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("code") != "0":
            logger.error(f"OKX K线获取失败: {data.get('msg')}")
            return 0.0

        candles = data["data"]
        if len(candles) < period + 1:
            return 0.0

        true_ranges = []
        for i in range(1, len(candles)):
            high = float(candles[i][2])
            low = float(candles[i][3])
            prev_close = float(candles[i-1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        if true_ranges:
            atr = sum(true_ranges[-period:]) / period
            return round(atr, 2)
        return 0.0
    except Exception as e:
        logger.error(f"ATR 计算异常: {e}")
        return 0.0
