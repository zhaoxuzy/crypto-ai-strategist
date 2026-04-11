import os
import time
import requests
from utils.logger import logger

class CoinGlassClient:
    def __init__(self):
        self.api_key = os.getenv("COINGLASS_API_KEY", "")
        self.base_url = "https://www.keystore.com.cn/api/v1/proxy/coinglass/v4"
        self.delay = 2.5

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

    # ---------- 清算热力图（model2，官方文档确认）----------
    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "range": "24h"
        }
        return self._request("api/futures/liquidation/heatmap/model2", params, silent_fail=True)

    def _parse_liquidation_matrix(self, raw_data: dict, current_price: float) -> dict:
        result = {
            "above_short_liquidation": "0",
            "below_long_liquidation": "0",
            "max_pain_price": "N/A",
            "nearest_cluster": {"direction": "N/A", "price": "N/A", "intensity": "N/A"}
        }

        if not isinstance(raw_data, dict):
            logger.warning("清算数据不是字典，无法解析")
            return result

        y_axis = raw_data.get("y_axis")
        liq_data = raw_data.get("liquidation_leverage_data")

        if not y_axis or not liq_data:
            logger.warning("清算矩阵缺少 y_axis 或 liquidation_leverage_data")
            return result

        # model2 返回的是稀疏矩阵：[x_index, y_index, intensity] 三元组列表
        if not isinstance(liq_data, list):
            logger.warning("liquidation_leverage_data 不是列表")
            return result

        total_long = 0.0
        total_short = 0.0
        pain_map = {}  # 价格 -> 清算强度累计

        for item in liq_data:
            if not isinstance(item, list) or len(item) < 3:
                continue
            x_idx = int(item[0])
            y_idx = int(item[1])
            intensity = float(item[2])

            if y_idx < 0 or y_idx >= len(y_axis):
                continue

            price = float(y_axis[y_idx])

            # 根据 x_idx 区分多空清算（通常 0=多头清算，1=空头清算）
            if x_idx == 0:
                if price < current_price:
                    total_long += intensity
            elif x_idx == 1:
                if price > current_price:
                    total_short += intensity
            else:
                continue

            # 累计该价格的清算强度
            pain_map[price] = pain_map.get(price, 0.0) + intensity

        # 找出最大痛点价格
        max_pain_price = None
        max_pain_value = 0.0
        for price, val in pain_map.items():
            if val > max_pain_value:
                max_pain_value = val
                max_pain_price = price

        # 找最近清算密集区
        nearest_cluster_price = None
        nearest_cluster_distance = float('inf')
        for price, val in pain_map.items():
            if val > 0:
                distance = abs(price - current_price)
                if distance < nearest_cluster_distance:
                    nearest_cluster_distance = distance
                    nearest_cluster_price = price

        result["above_short_liquidation"] = f"{total_short:,.0f}"
        result["below_long_liquidation"] = f"{total_long:,.0f}"
        if max_pain_price is not None:
            result["max_pain_price"] = f"{max_pain_price:.1f}"
        if nearest_cluster_price is not None:
            direction = "上" if nearest_cluster_price > current_price else "下"
            intensity_val = pain_map.get(nearest_cluster_price, 0)
            intensity = min(5, int(intensity_val / 5000000) + 1)
            result["nearest_cluster"] = {
                "direction": direction,
                "price": f"{nearest_cluster_price:.1f}",
                "intensity": str(intensity)
            }

        logger.info(f"清算解析: 上方空头={result['above_short_liquidation']}, 下方多头={result['below_long_liquidation']}, 痛点={result['max_pain_price']}")
        return result

    # ---------- 其他接口 ----------
    def get_open_interest_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/open-interest/history", params)

    def get_funding_rate_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 1}
        return self._request("api/futures/funding-rate/history", params)

    def get_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/global-long-short-account-ratio/history", params)

    def get_taker_volume_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/v2/taker-buy-sell-volume/history", params, silent_fail=True)

    def get_option_max_pain(self, symbol: str = "BTC"):
        params = {"exchange": "Deribit", "symbol": symbol.upper()}
        return self._request("api/option/max-pain", params, silent_fail=True)

    def get_cvd_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "5m", "limit": 24}
        return self._request("api/futures/cvd/history", params, silent_fail=True)

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

    def get_all_data(self, symbol: str = "BTC", current_price: float = None) -> dict:
        if current_price is None:
            current_price = 70000.0

        data = {}
        heatmap_raw = self.get_liquidation_heatmap(symbol)
        liq_data = self._parse_liquidation_matrix(heatmap_raw, current_price)
        data.update(liq_data)

        oi_history = self.get_open_interest_history(symbol)
        oi_change = "N/A"
        if isinstance(oi_history, list) and len(oi_history) >= 2:
            last_close = self._get_close_from_candle(oi_history[-1])
            prev_close = self._get_close_from_candle(oi_history[-2])
            if prev_close > 0:
                oi_change = f"{((last_close - prev_close) / prev_close * 100):.2f}%"
        data["oi_change_24h"] = oi_change

        funding_history = self.get_funding_rate_history(symbol)
        funding_rate = "N/A"
        if isinstance(funding_history, list) and len(funding_history) > 0:
            funding_rate = self._get_close_from_candle(funding_history[-1])
        data["funding_rate"] = funding_rate

        ls_history = self.get_long_short_ratio_history(symbol)
        ls_ratio = "N/A"
        if isinstance(ls_history, list) and len(ls_history) > 0:
            ls_ratio = self._get_close_from_candle(ls_history[-1])
        data["long_short_ratio"] = ls_ratio

        taker_history = self.get_taker_volume_history(symbol)
        taker_ratio = "N/A"
        if isinstance(taker_history, list) and len(taker_history) > 0:
            buy_vol, sell_vol = self._get_buy_sell_volumes(taker_history[-1])
            total = buy_vol + sell_vol
            if total > 0:
                taker_ratio = f"{(buy_vol / total):.2f}"
        data["taker_ratio"] = taker_ratio

        max_pain_data = self.get_option_max_pain(symbol)
        skew_value = "N/A"
        if isinstance(max_pain_data, dict):
            skew_value = max_pain_data.get("maxPain", max_pain_data.get("max_pain", "N/A"))
        elif isinstance(max_pain_data, list) and max_pain_data:
            latest = max_pain_data[-1]
            if isinstance(latest, dict):
                skew_value = latest.get("maxPain", latest.get("max_pain", "N/A"))
        data["skew"] = skew_value

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
