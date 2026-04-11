import os
import time
import requests
from utils.logger import logger

class CoinGlassClient:
    def __init__(self):
        self.api_key = os.getenv("COINGLASS_API_KEY", "")
        # 使用 KeyStore 代理
        self.base_url = "https://www.keystore.com.cn/api/v1/proxy/coinglass/v4"
        self.delay = 6.0

    def _request(self, endpoint: str, params: dict = None, silent_fail: bool = False) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "accept": "application/json",
            "X-Api-Key": self.api_key
        }
        params = params or {}
        logger.info(f"请求 CoinGlass: {endpoint} | params={params}")
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            time.sleep(self.delay)
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

    # ---------- 清算热力图 (使用 OKX) ----------
    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",                 # 改为 OKX，全大写
            "symbol": f"{symbol}USDT",
            "range": "24h"
        }
        return self._request("api/futures/liquidation/heatmap/model2", params, silent_fail=True)

    def _parse_liquidation_data(self, raw_data: dict) -> dict:
        if not isinstance(raw_data, dict):
            return {}
        summary = raw_data.get("summary")
        if isinstance(summary, dict):
            return summary
        if "shortLiquidationTotal" in raw_data:
            return raw_data
        # 尝试从 data 数组中提取聚合值（某些版本）
        data_list = raw_data.get("data", [])
        if isinstance(data_list, list):
            total_short = 0
            total_long = 0
            for item in data_list:
                if isinstance(item, dict):
                    total_short += item.get("shortLiquidation", 0) or 0
                    total_long += item.get("longLiquidation", 0) or 0
            if total_short or total_long:
                return {
                    "shortLiquidationTotal": total_short,
                    "longLiquidationTotal": total_long
                }
        return {}

    # ---------- 其他接口改用 OKX 以保证一致性 ----------
    def get_open_interest_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/open-interest/history", params)

    def get_funding_rate_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 1
        }
        return self._request("api/futures/funding-rate/history", params)

    def get_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}USDT",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/global-long-short-account-ratio/history", params)

    def get_option_max_pain(self, symbol: str = "BTC"):
        params = {
            "exchange": "All",
            "symbol": f"{symbol}USDT"
        }
        return self._request("api/option/max-pain", params, silent_fail=True)

    @staticmethod
    def _get_close_from_candle(candle) -> float:
        if isinstance(candle, list) and len(candle) >= 5:
            return float(candle[4])
        elif isinstance(candle, dict):
            return float(candle.get("close", 0))
        return 0.0

    def get_all_data(self, symbol: str = "BTC") -> dict:
        data = {}

        # 1. 清算热力图
        heatmap_raw = self.get_liquidation_heatmap(symbol)
        summary = self._parse_liquidation_data(heatmap_raw)
        data["above_short_liquidation"] = summary.get("shortLiquidationTotal", "N/A")
        data["below_long_liquidation"] = summary.get("longLiquidationTotal", "N/A")
        data["max_pain_price"] = summary.get("maxPain", "N/A")
        data["nearest_cluster"] = {
            "direction": summary.get("nearestClusterDirection", "N/A"),
            "price": summary.get("nearestClusterPrice", "N/A"),
            "intensity": summary.get("nearestClusterIntensity", "N/A")
        }

        # 2. 持仓量
        oi_history = self.get_open_interest_history(symbol)
        oi_change = "N/A"
        if isinstance(oi_history, list) and len(oi_history) >= 2:
            last_close = self._get_close_from_candle(oi_history[-1])
            prev_close = self._get_close_from_candle(oi_history[-2])
            if prev_close > 0:
                oi_change = f"{((last_close - prev_close) / prev_close * 100):.2f}%"
        data["oi_change_24h"] = oi_change

        # 3. 资金费率
        funding_history = self.get_funding_rate_history(symbol)
        funding_rate = "N/A"
        if isinstance(funding_history, list) and len(funding_history) > 0:
            funding_rate = self._get_close_from_candle(funding_history[-1])
        data["funding_rate"] = funding_rate

        # 4. 多空比
        ls_history = self.get_long_short_ratio_history(symbol)
        ls_ratio = "N/A"
        if isinstance(ls_history, list) and len(ls_history) > 0:
            ls_ratio = self._get_close_from_candle(ls_history[-1])
        data["long_short_ratio"] = ls_ratio

        data["taker_ratio"] = "N/A"

        # 5. 期权最大痛点
        max_pain_data = self.get_option_max_pain(symbol)
        skew_value = "N/A"
        if isinstance(max_pain_data, dict):
            skew_value = max_pain_data.get("maxPain", max_pain_data.get("max_pain", "N/A"))
        elif isinstance(max_pain_data, list) and max_pain_data:
            latest = max_pain_data[-1]
            if isinstance(latest, dict):
                skew_value = latest.get("maxPain", "N/A")
        data["skew"] = skew_value

        data["cvd_signal"] = "N/A"
        data["cvd_slope"] = "N/A"

        logger.info(f"清算数据解析结果: shortLiq={data['above_short_liquidation']}, longLiq={data['below_long_liquidation']}")
        return data
