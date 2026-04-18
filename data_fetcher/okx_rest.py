import os
import time
import requests
from utils.logger import logger

def get_current_price(inst_id: str) -> float:
    """获取合约最新价"""
    try:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("code") == "0":
            return float(data["data"][0]["last"])
        logger.warning(f"OKX 获取价格失败: {data}")
        return 0.0
    except Exception as e:
        logger.error(f"OKX 请求异常: {e}")
        return 0.0


def get_klines(inst_id: str, bar: str = "1H", limit: int = 70) -> list:
    """
    获取 K 线数据
    bar: 1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M
    """
    try:
        url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("code") == "0":
            klines = data["data"]
            klines.reverse()  # OKX 返回是倒序，反转成时间升序
            return klines
        logger.warning(f"OKX 获取K线失败: {data}")
        return []
    except Exception as e:
        logger.error(f"OKX K线请求异常: {e}")
        return []


def calculate_ema(klines: list, period: int) -> float:
    """计算 EMA，返回最新值"""
    if not klines or len(klines) < period:
        return 0.0
    closes = [float(k[4]) for k in klines[-period*2:] if len(k) >= 5]
    if len(closes) < period:
        return 0.0
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calculate_ema_slope(klines: list, period: int, lookback: int = 5) -> float:
    """计算 EMA 斜率（最近 lookback 根K线的线性回归斜率）"""
    if not klines or len(klines) < period + lookback:
        return 0.0
    ema_values = []
    for i in range(len(klines) - lookback, len(klines)):
        ema = calculate_ema(klines[:i+1], period)
        if ema > 0:
            ema_values.append(ema)
    if len(ema_values) < 2:
        return 0.0
    n = len(ema_values)
    x_mean = (n - 1) / 2
    y_mean = sum(ema_values) / n
    numerator = sum((i - x_mean) * (ema_values[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    if denominator == 0:
        return 0.0
    slope = numerator / denominator
    return round(slope, 4)


def calculate_atr(inst_id: str, timeframe: str = "1H", period: int = 14, limit: int = 30) -> float:
    """
    计算 ATR
    timeframe: 1H/4H/1D 等，传递给 get_klines
    """
    klines = get_klines(inst_id, bar=timeframe, limit=limit)
    if not klines or len(klines) < period + 1:
        logger.warning(f"K线数据不足，无法计算 ATR (需要至少 {period+1} 根，实际 {len(klines)})")
        return 0.0
    true_ranges = []
    for i in range(1, len(klines)):
        if len(klines[i]) < 5 or len(klines[i-1]) < 5:
            continue
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    if len(true_ranges) < period:
        return 0.0
    atr = sum(true_ranges[-period:]) / period
    return round(atr, 2)


def calculate_atr_percentile(klines: list, current_atr: float, lookback: int = 20) -> float:
    """计算当前 ATR 在历史上的百分位"""
    if not klines or len(klines) < lookback:
        return 50.0
    atr_values = []
    for i in range(lookback, len(klines)):
        tr_list = []
        for j in range(i - lookback + 1, i + 1):
            if j == 0 or len(klines[j]) < 5 or len(klines[j-1]) < 5:
                continue
            high = float(klines[j][2])
            low = float(klines[j][3])
            prev_close = float(klines[j-1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)
        if len(tr_list) >= 14:
            atr_values.append(sum(tr_list[-14:]) / 14)
    if not atr_values:
        return 50.0
    count_below = sum(1 for a in atr_values if a < current_atr)
    percentile = (count_below / len(atr_values)) * 100
    return round(percentile, 1)
