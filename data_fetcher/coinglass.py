import os
import time
import requests
from utils.logger import logger

class CoinGlassClient:
    def __init__(self):
        self.api_key = os.getenv("COINGLASS_API_KEY", "")
        self.base_url = "https://www.keystore.com.cn/api/v1/proxy/coinglass/v4"

    def _request(self, endpoint: str, params: dict = None) -> dict:
        """发送 GET 请求，每次请求后强制延迟 0.5 秒以避免速率限制"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "accept": "application/json",
            "X-Api-Key": self.api_key
        }
        params = params or {}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            # 速率限制保护
            time.sleep(0.5)
            data = resp.json()
            if data.get("code") not in (0, "0"):
                logger.error(f"CoinGlass API 错误: {data.get('msg', data)}")
                return {}
            return data.get("data", {})
        except Exception as e:
            logger.error(f"CoinGlass 请求失败: {e}")
            return {}

    # ---------- 核心接口（已根据文档验证）----------
    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        """清算热力图 Model2 - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "range": "24h"
        }
        return self._request("api/futures/liquidation/heatmap/model2", params)

    def get_open_interest_history(self, symbol: str = "BTC"):
        """持仓量 OHLC 历史 - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/openInterest/ohlc-history", params)

    def get_funding_rate_history(self, symbol: str = "BTC"):
        """资金费率 OHLC 历史 - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 1
        }
        return self._request("api/futures/fundingRate/ohlc-history", params)

    def get_long_short_ratio_history(self, symbol: str = "BTC"):
        """全局多空比历史 - 官方端点"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/global-long-short-account-ratio/history", params)

    def get_taker_volume_history(self, symbol: str = "BTC"):
        """主动买卖量历史 - 官方端点（V4中无/v2）"""
        params = {
            "exchange": "Binance",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/taker-buy-sell-volume/history", params)

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

    # ---------- 数据聚合（仅包含已验证接口）----------
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

        # 6. 期权信号与CVD暂用占位符（后续可按需扩展）
        data["skew"] = "N/A"
        data["cvd_signal"] = "N/A"
        data["cvd_slope"] = "N/A"

        return data
