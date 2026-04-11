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

    # ---------- 清算热力图（原始矩阵）----------
    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "range": "24h"
        }
        return self._request("api/futures/liquidation/heatmap/model2", params, silent_fail=True)

    def _parse_liquidation_matrix(self, raw_data: dict, current_price: float) -> dict:
        """
        从原始热力图矩阵中提取：
        - 上方空头清算总量
        - 下方多头清算总量
        - 最大痛点价格
        - 最近清算密集区方向/价格/强度
        """
        result = {
            "above_short_liquidation": "N/A",
            "below_long_liquidation": "N/A",
            "max_pain_price": "N/A",
            "nearest_cluster": {"direction": "N/A", "price": "N/A", "intensity": "N/A"}
        }

        if not isinstance(raw_data, dict):
            return result

        y_axis = raw_data.get("y_axis", [])
        matrix = raw_data.get("liquidation_leverage_data", [])

        if not y_axis or not matrix or len(y_axis) != len(matrix):
            logger.warning("清算矩阵数据格式异常，无法解析")
            return result

        # 矩阵每一行对应一个价格档位，包含 [long_liquidation, short_liquidation]
        total_long = 0.0
        total_short = 0.0
        max_pain_price = None
        max_pain_value = 0.0

        # 找最近的清算密集区（强度基于清算金额的绝对值）
        nearest_cluster_idx = None
        nearest_cluster_distance = float('inf')

        for i, price in enumerate(y_axis):
            row = matrix[i]
            if not isinstance(row, list) or len(row) < 2:
                continue
            long_liq = float(row[0]) if row[0] is not None else 0.0
            short_liq = float(row[1]) if row[1] is not None else 0.0

            if price > current_price:
                total_short += short_liq
            elif price < current_price:
                total_long += long_liq

            total_liq = long_liq + short_liq
            if total_liq > max_pain_value:
                max_pain_value = total_liq
                max_pain_price = price

            # 寻找离当前价最近且有清算量的档位
            if total_liq > 0:
                distance = abs(price - current_price)
                if distance < nearest_cluster_distance:
                    nearest_cluster_distance = distance
                    nearest_cluster_idx = i

        result["above_short_liquidation"] = f"{total_short:,.0f}" if total_short > 0 else "N/A"
        result["below_long_liquidation"] = f"{total_long:,.0f}" if total_long > 0 else "N/A"
        result["max_pain_price"] = f"{max_pain_price:.1f}" if max_pain_price is not None else "N/A"

        if nearest_cluster_idx is not None:
            price = y_axis[nearest_cluster_idx]
            row = matrix[nearest_cluster_idx]
            long_liq = float(row[0]) if row[0] else 0.0
            short_liq = float(row[1]) if row[1] else 0.0
            direction = "上" if price > current_price else "下"
            intensity = min(5, int((long_liq + short_liq) / 5000000) + 1)  # 简单强度分级
            result["nearest_cluster"] = {
                "direction": direction,
                "price": f"{price:.1f}",
                "intensity": str(intensity)
            }

        logger.info(f"清算解析: 上方空头={result['above_short_liquidation']}, 下方多头={result['below_long_liquidation']}, 痛点={result['max_pain_price']}")
        return result

    # ---------- 持仓量历史 ----------
    def get_open_interest_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/open-interest/history", params)

    # ---------- 资金费率历史 ----------
    def get_funding_rate_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "1h",
            "limit": 1
        }
        return self._request("api/futures/funding-rate/history", params)

    # ---------- 多空比 ----------
    def get_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/global-long-short-account-ratio/history", params)

    # ---------- 主动买卖量 ----------
    def get_taker_volume_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "1h",
            "limit": 24
        }
        return self._request("api/futures/v2/taker-buy-sell-volume/history", params, silent_fail=True)

    # ---------- 期权最大痛点 ----------
    def get_option_max_pain(self, symbol: str = "BTC"):
        params = {
            "exchange": "Deribit",
            "symbol": symbol.upper()
        }
        return self._request("api/option/max-pain", params, silent_fail=True)

    # ---------- CVD ----------
    def get_cvd_history(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "interval": "5m",
            "limit": 24
        }
        return self._request("api/futures/cvd/history", params, silent_fail=True)

    # ---------- 通用辅助函数 ----------
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
            # 如果未传入价格，这里先给一个默认值，实际调用时会从主程序传入
            current_price = 70000.0

        data = {}

        # 1. 清算热力图（矩阵解析）
        heatmap_raw = self.get_liquidation_heatmap(symbol)
        liq_data = self._parse_liquidation_matrix(heatmap_raw, current_price)
        data.update(liq_data)

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

        return data
