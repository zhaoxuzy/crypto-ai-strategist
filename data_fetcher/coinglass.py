import os
import time
import requests
from utils.logger import logger

class CoinGlassClient:
    def __init__(self):
        self.api_key = os.getenv("COINGLASS_API_KEY", "")
        self.base_url = "https://www.keystore.com.cn/api/v1/proxy/coinglass/v4"

    def _request(self, endpoint: str, params: dict = None, silent_fail: bool = False) -> dict:
        """发送 GET 请求，每次请求后强制延迟 0.6 秒以避免速率限制（10 req/min）"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "accept": "application/json",
            "X-Api-Key": self.api_key
        }
        params = params or {}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            time.sleep(0.6)  # 确保每分钟不超过10次请求
            data = resp.json()
            if data.get("code") not in (0, "0"):
                msg = f"CoinGlass API 错误: {data.get('msg', data)}"
                if silent_fail:
                    logger.warning(msg)
                else:
                    logger.error(msg)
                return {}
            return data.get("data", {})
        except Exception as e:
            msg = f"CoinGlass 请求失败: {e}"
            if silent_fail:
                logger.warning(msg)
            else:
                logger.error(msg)
            return {}

    # ---------- 1. 清算热力图（官方端点）----------
    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        """Pair Liquidation Heatmap Model2 - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "range": "24h"
        }
        return self._request("api/futures/liquidation/heatmap/model2", params, silent_fail=True)

    # ---------- 2. 持仓量历史（官方端点）----------
    def get_open_interest_history(self, symbol: str = "BTC"):
        """Open Interest OHLC history - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/open-interest/history", params)

    # ---------- 3. 资金费率历史（官方端点）----------
    def get_funding_rate_history(self, symbol: str = "BTC"):
        """Funding Rate OHLC History - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 1
        }
        return self._request("api/futures/funding-rate/history", params)

    # ---------- 4. 多空比（官方端点）----------
    def get_long_short_ratio_history(self, symbol: str = "BTC"):
        """Global Long/Short Account Ratio - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/global-long-short-account-ratio/history", params)

    # ---------- 5. 主动买卖量（官方端点 v2）----------
    def get_taker_volume_history(self, symbol: str = "BTC"):
        """Pair Taker Buy/Sell History (v2) - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/v2/taker-buy-sell-volume/history", params)

    # ---------- 6. 期权最大痛点（替代偏度，官方端点）----------
    def get_option_max_pain(self, symbol: str = "BTC"):
        """Option Max Pain - 官方端点"""
        params = {
            "exchange": "All",
            "symbol": f"{symbol}USDT"
        }
        return self._request("api/option/max-pain", params, silent_fail=True)

    # ---------- 7. 期货 CVD（官方端点，用于资金流向）----------
    def get_cvd_history(self, symbol: str = "BTC"):
        """Cumulative Volume Delta (CVD) - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "interval": "5m",
            "limit": 24
        }
        return self._request("api/futures/cvd/history", params, silent_fail=True)

    # ---------- 辅助解析函数 ----------
    @staticmethod
    def _get_close_from_candle(candle) -> float:
        if isinstance(candle, list) and len(candle) >= 5:
            return float(candle[4])
        elif isinstance(candle, dict):
            return float(candle.get("close", 0))
        return 0.0

    @staticmethod
    def _get_buy_sell_volumes(candle):
        if isinstance(candle, list):
            buy = float(candle[4]) if len(candle) > 4 else 0.0
            sell = float(candle[5]) if len(candle) > 5 else 0.0
            return buy, sell
        elif isinstance(candle, dict):
            buy = float(candle.get("buyVolume", 0))
            sell = float(candle.get("sellVolume", 0))
            return buy, sell
        return 0.0, 0.0

    # ---------- 数据聚合 ----------
    def get_all_data(self, symbol: str = "BTC") -> dict:
        data = {}

        # 1. 清算热力图
        heatmap = self.get_liquidation_heatmap(symbol)
        summary = heatmap.get("summary", {}) if isinstance(heatmap, dict) else {}
        data["above_short_liquidation"] = summary.get("shortLiquidationTotal", "N/A")
        data["below_long_liquidation"] = summary.get("longLiquidationTotal", "N/A")
        data["max_pain_price"] = summary.get("maxPain", "N/A")
        data["nearest_cluster"] = {
            "direction": summary.get("nearestClusterDirection", "N/A"),
            "price": summary.get("nearestClusterPrice", "N/A"),
            "intensity": summary.get("nearestClusterIntensity", "N/A")
        }

        # 2. 持仓量24h变化
        oi_history = self.get_open_interest_history(symbol)
        oi_change = "N/A"
        if isinstance(oi_history, list) and len(oi_history) >= 2:
            last_close = self._get_close_from_candle(oi_history[-1])
            prev_close = self._get_close_from_candle(oi_history[-2])
            if prev_close > 0:
                oi_change = f"{((last_close - prev_close) / prev_close * 100):.2f}%"
        data["oi_change_24h"] = oi_change

        # 3. 最新资金费率
        funding_history = self.get_funding_rate_history(symbol)
        funding_rate = "N/A"
        if isinstance(funding_history, list) and len(funding_history) > 0:
            funding_rate = self._get_close_from_candle(funding_history[-1])
        data["funding_rate"] = funding_rate

        # 4. 最新多空比
        ls_history = self.get_long_short_ratio_history(symbol)
        ls_ratio = "N/A"
        if isinstance(ls_history, list) and len(ls_history) > 0:
            ls_ratio = self._get_close_from_candle(ls_history[-1])
        data["long_short_ratio"] = ls_ratio

        # 5. 主动吃单比率
        taker_history = self.get_taker_volume_history(symbol)
        taker_ratio = "N/A"
        if isinstance(taker_history, list) and len(taker_history) > 0:
            buy_vol, sell_vol = self._get_buy_sell_volumes(taker_history[-1])
            total = buy_vol + sell_vol
            if total > 0:
                taker_ratio = f"{(buy_vol / total):.2f}"
        data["taker_ratio"] = taker_ratio

        # 6. 期权最大痛点（替代偏度）
        max_pain_data = self.get_option_max_pain(symbol)
        skew_value = "N/A"
        if isinstance(max_pain_data, dict):
            skew_value = max_pain_data.get("maxPain", "N/A")
        data["skew"] = skew_value

        # 7. CVD 斜率信号（基于期货 CVD）
        cvd_signal = "N/A"
        cvd_slope = "N/A"
        cvd_history = self.get_cvd_history(symbol)
        if isinstance(cvd_history, list) and len(cvd_history) >= 12:
            recent = cvd_history[-12:]
            values = []
            for item in recent:
                if isinstance(item, list) and len(item) >= 5:
                    values.append(float(item[4]))
                elif isinstance(item, dict):
                    values.append(float(item.get("close", 0)))
            if len(values) >= 2:
                n = len(values)
                x_mean = (n - 1) / 2
                y_mean = sum(values) / n
                numerator = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
                denominator = sum((i - x_mean) ** 2 for i in range(n))
                if denominator != 0:
                    slope = numerator / denominator
                    cvd_slope = round(slope, 4)
                    if slope > 10:
                        cvd_signal = "bullish"
                    elif slope > 2:
                        cvd_signal = "slightly_bullish"
                    elif slope < -10:
                        cvd_signal = "bearish"
                    elif slope < -2:
                        cvd_signal = "slightly_bearish"
                    else:
                        cvd_signal = "neutral"
        data["cvd_signal"] = cvd_signal
        data["cvd_slope"] = cvd_slope

        return data
