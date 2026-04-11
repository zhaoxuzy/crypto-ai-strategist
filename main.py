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

    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "range": "24h"
        }
        return self._request("api/futures/liquidation/heatmap/model2", params, silent_fail=True)

    def _parse_liquidation_matrix(self, raw_data: dict, current_price: float) -> dict:
        result = {
            "above_short_liquidation": "N/A",
            "below_long_liquidation": "N/A",
            "max_pain_price": "N/A",
            "nearest_cluster": {"direction": "N/A", "price": "N/A", "intensity": "N/A"}
        }

        if not isinstance(raw_data, dict):
            logger.warning("[DEBUG] 清算数据不是字典类型")
            return result

        # 打印详细的调试信息
        logger.info(f"[DEBUG] 清算数据顶层 keys: {list(raw_data.keys())}")
        # 尝试打印 y_axis 的长度和样本
        y_axis = raw_data.get("y_axis")
        if y_axis is not None:
            logger.info(f"[DEBUG] y_axis 类型: {type(y_axis)}, 长度: {len(y_axis) if hasattr(y_axis, '__len__') else 'N/A'}")
            if hasattr(y_axis, '__len__') and len(y_axis) > 0:
                logger.info(f"[DEBUG] y_axis 前5个值: {y_axis[:5]}")
        # 检查其他可能的键
        for key in ["data", "list", "matrix", "liquidation_leverage_data"]:
            val = raw_data.get(key)
            if val is not None:
                logger.info(f"[DEBUG] 发现键 '{key}', 类型: {type(val)}")

        # 尝试多种数据结构
        matrix = None
        y_axis = raw_data.get("y_axis")
        # 常见的矩阵键名
        possible_matrix_keys = ["liquidation_leverage_data", "data", "matrix", "list", "values"]
        for key in possible_matrix_keys:
            if key in raw_data:
                matrix = raw_data.get(key)
                break

        if y_axis is None or matrix is None:
            # 可能数据在 data 字段内
            inner_data = raw_data.get("data")
            if isinstance(inner_data, dict):
                y_axis = inner_data.get("y_axis")
                matrix = inner_data.get("liquidation_leverage_data") or inner_data.get("data")
            if y_axis is None or matrix is None:
                logger.warning("[DEBUG] 未找到 y_axis 或 matrix，尝试打印整个 raw_data 的前500字符")
                logger.warning(f"[DEBUG] raw_data: {json.dumps(raw_data, ensure_ascii=False)[:500]}")
                return result

        # 确保长度匹配
        if not hasattr(y_axis, '__len__') or not hasattr(matrix, '__len__'):
            logger.warning("[DEBUG] y_axis 或 matrix 没有长度")
            return result
        if len(y_axis) != len(matrix):
            logger.warning(f"[DEBUG] y_axis 长度({len(y_axis)}) 与 matrix 长度({len(matrix)}) 不匹配")
            return result

        total_long = 0.0
        total_short = 0.0
        max_pain_price = None
        max_pain_value = 0.0
        nearest_cluster_idx = None
        nearest_cluster_distance = float('inf')

        for i, price in enumerate(y_axis):
            row = matrix[i]
            # row 可能是列表 [long, short] 或字典 {"long":..., "short":...}
            if isinstance(row, list):
                if len(row) >= 2:
                    long_liq = float(row[0]) if row[0] is not None else 0.0
                    short_liq = float(row[1]) if row[1] is not None else 0.0
                else:
                    continue
            elif isinstance(row, dict):
                long_liq = float(row.get("longLiquidation", row.get("long", 0)))
                short_liq = float(row.get("shortLiquidation", row.get("short", 0)))
            else:
                continue

            price_f = float(price)
            if price_f > current_price:
                total_short += short_liq
            elif price_f < current_price:
                total_long += long_liq

            total_liq = long_liq + short_liq
            if total_liq > max_pain_value:
                max_pain_value = total_liq
                max_pain_price = price_f

            if total_liq > 0:
                distance = abs(price_f - current_price)
                if distance < nearest_cluster_distance:
                    nearest_cluster_distance = distance
                    nearest_cluster_idx = i

        result["above_short_liquidation"] = f"{total_short:,.0f}" if total_short > 0 else "N/A"
        result["below_long_liquidation"] = f"{total_long:,.0f}" if total_long > 0 else "N/A"
        result["max_pain_price"] = f"{max_pain_price:.1f}" if max_pain_price is not None else "N/A"

        if nearest_cluster_idx is not None:
            price = float(y_axis[nearest_cluster_idx])
            row = matrix[nearest_cluster_idx]
            if isinstance(row, list) and len(row) >= 2:
                long_liq = float(row[0]) if row[0] else 0.0
                short_liq = float(row[1]) if row[1] else 0.0
            else:
                long_liq = float(row.get("long", 0))
                short_liq = float(row.get("short", 0))
            direction = "上" if price > current_price else "下"
            intensity = min(5, int((long_liq + short_liq) / 5000000) + 1)
            result["nearest_cluster"] = {
                "direction": direction,
                "price": f"{price:.1f}",
                "intensity": str(intensity)
            }

        logger.info(f"清算解析: 上方空头={result['above_short_liquidation']}, 下方多头={result['below_long_liquidation']}, 痛点={result['max_pain_price']}")
        return result

    # ---------- 其他接口保持不变 ----------
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
