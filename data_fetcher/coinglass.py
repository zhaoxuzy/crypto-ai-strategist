import os
import time
import requests
import json
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

    # ---------- 清算热力图 (model2) ----------
    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "range": "24h"
        }
        return self._request("api/futures/liquidation/heatmap/model2", params, silent_fail=True)

    def _parse_liquidation_heatmap(self, raw_data: dict, current_price: float) -> dict:
        """
        解析 model2 返回的原始热力图数据，计算上下方清算总额及密集区信息。
        返回包含 summary 格式的字典。
        """
        result = {
            "above_short_liquidation": "N/A",
            "below_long_liquidation": "N/A",
            "max_pain_price": "N/A",
            "nearest_cluster": {
                "direction": "N/A",
                "price": "N/A",
                "intensity": "N/A"
            }
        }

        if not isinstance(raw_data, dict):
            return result

        y_axis = raw_data.get("y_axis", [])          # 价格轴数组，升序排列
        liquidation_data = raw_data.get("liquidation_leverage_data", {})

        # 如果没有价格轴或清算数据，直接返回
        if not y_axis or not isinstance(liquidation_data, dict):
            return result

        # 查找当前价格在 y_axis 中的索引位置
        current_idx = None
        for i, price in enumerate(y_axis):
            if price >= current_price:
                current_idx = i
                break

        if current_idx is None:
            current_idx = len(y_axis) - 1

        # 上方空头清算 (价格高于当前价)
        short_total = 0.0
        # 下方多头清算 (价格低于当前价)
        long_total = 0.0

        # 遍历清算数据中的每个杠杆档位（如 "5x", "10x" 等）
        max_intensity = 0
        max_pain_price = None
        nearest_cluster = None
        min_distance = float('inf')

        for leverage, values in liquidation_data.items():
            if not isinstance(values, list) or len(values) != len(y_axis):
                continue

            for i, val in enumerate(values):
                if val is None:
                    continue
                amount = float(val)
                price = y_axis[i]

                if i > current_idx:
                    short_total += amount
                elif i < current_idx:
                    long_total += amount

                # 寻找最大清算痛点（清算金额最大的价格）
                if amount > max_intensity:
                    max_intensity = amount
                    max_pain_price = price

                # 寻找最近清算密集区（清算金额 > 0 且离当前价最近）
                if amount > 0:
                    distance = abs(price - current_price)
                    if distance < min_distance:
                        min_distance = distance
                        direction = "上方" if price > current_price else "下方"
                        intensity_rating = min(5, int(amount / 500000) + 1)  # 简单强度分级
                        nearest_cluster = {
                            "direction": direction,
                            "price": price,
                            "intensity": intensity_rating
                        }

        result["above_short_liquidation"] = f"{short_total:,.0f}" if short_total > 0 else "N/A"
        result["below_long_liquidation"] = f"{long_total:,.0f}" if long_total > 0 else "N/A"
        result["max_pain_price"] = f"{max_pain_price:.1f}" if max_pain_price is not None else "N/A"
        if nearest_cluster:
            result["nearest_cluster"] = nearest_cluster

        return result

    # ---------- 其他接口保持不变 ----------
    def get_open_interest_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/open-interest/history", params)

    def get_funding_rate_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "1h",
            "limit": 1
        }
        return self._request("api/futures/funding-rate/history", params)

    def get_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/global-long-short-account-ratio/history", params)

    def get_taker_volume_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/v2/taker-buy-sell-volume/history", params, silent_fail=True)

    def get_option_max_pain(self, symbol: str = "BTC"):
        params = {
            "exchange": "Deribit",
            "symbol": symbol.upper()
        }
        return self._request("api/option/max-pain", params, silent_fail=True)

    def get_cvd_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "5m",
            "limit": 24
        }
        return self._request("api/futures/cvd/history", params, silent_fail=True)

    # ---------- 辅助解析 ----------
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
    def get_all_data(self, symbol: str = "BTC", current_price: float = None) -> dict:
        if current_price is None:
            # 如果没有传入当前价格，则从外部获取，但这里我们假设调用方会传入
            current_price = 0.0
        data = {}

        # 1. 清算热力图（使用新的解析器）
        heatmap_raw = self.get_liquidation_heatmap(symbol)
        if current_price > 0:
            parsed = self._parse_liquidation_heatmap(heatmap_raw, current_price)
        else:
            parsed = {}
        data["above_short_liquidation"] = parsed.get("above_short_liquidation", "N/A")
        data["below_long_liquidation"] = parsed.get("below_long_liquidation", "N/A")
        data["max_pain_price"] = parsed.get("max_pain_price", "N/A")
        data["nearest_cluster"] = parsed.get("nearest_cluster", {
            "direction": "N/A", "price": "N/A", "intensity": "N/A"
        })

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

        # 6. 期权最大痛点
        max_pain_data = self.get_option_max_pain(symbol)
        skew_value = "N/A"
        if isinstance(max_pain_data, dict):
            skew_value = max_pain_data.get("maxPain", max_pain_data.get("max_pain", "N/A"))
        elif isinstance(max_pain_data, list) and max_pain_data:
            latest = max_pain_data[-1]
            if isinstance(latest, dict):
                skew_value = latest.get("maxPain", latest.get("max_pain", "N/A"))
        data["skew"] = skew_value

        # 7. CVD 斜率信号
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

        logger.info(f"清算数据解析结果: shortLiq={data['above_short_liquidation']}, longLiq={data['below_long_liquidation']}")
        return data
