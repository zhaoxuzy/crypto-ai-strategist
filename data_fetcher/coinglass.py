import os
import time
import requests
from utils.logger import logger

class CoinGlassClient:
    def __init__(self):
        self.api_key = os.getenv("COINGLASS_API_KEY", "")
        self.base_url = "https://www.keystore.com.cn/api/v1/proxy/coinglass/v4"
        self.delay = 2.5
        self.primary_exchange = "OKX"
        self.backup_exchanges = ["Bybit"]

    def _request(self, endpoint: str, params: dict = None, max_retries: int = 3, allow_backup: bool = True) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "accept": "application/json",
            "X-Api-Key": self.api_key
        }
        base_params = params.copy() if params else {}
        exchanges_to_try = [self.primary_exchange] + (self.backup_exchanges if allow_backup else [])

        last_error = None

        for exchange in exchanges_to_try:
            current_params = base_params.copy()
            if "exchange" in current_params:
                current_params["exchange"] = exchange

            for attempt in range(max_retries):
                try:
                    logger.info(f"请求 CoinGlass: {endpoint} | exchange={exchange} | params={current_params}" + 
                                (f" (重试 {attempt+1}/{max_retries})" if attempt > 0 else ""))
                    resp = requests.get(url, params=current_params, headers=headers, timeout=15)
                    time.sleep(self.delay)
                    data = resp.json()
                    if data.get("code") in (0, "0"):
                        return data.get("data", {})
                    else:
                        msg = f"CoinGlass API 错误: {data.get('msg', data)}"
                        last_error = msg
                        if attempt < max_retries - 1:
                            wait_time = 2 ** (attempt + 1)
                            logger.warning(f"{msg}，{wait_time}秒后重试...")
                            time.sleep(wait_time)
                            continue
                        else:
                            logger.warning(f"{exchange} 重试{max_retries}次后仍失败: {msg}")
                            break
                except requests.exceptions.Timeout as e:
                    last_error = f"请求超时: {e}"
                    if attempt < max_retries - 1:
                        wait_time = 2 ** (attempt + 1)
                        logger.warning(f"请求超时，{wait_time}秒后重试... ({attempt+1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"{exchange} 重试{max_retries}次后仍超时")
                        break
                except Exception as e:
                    last_error = f"请求异常: {e}"
                    if attempt < max_retries - 1:
                        wait_time = 2 ** (attempt + 1)
                        logger.warning(f"请求异常，{wait_time}秒后重试... ({attempt+1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"{exchange} 重试{max_retries}次后仍异常")
                        break

        raise RuntimeError(f"CoinGlass 数据获取失败，所有尝试均无效。最后错误: {last_error}")

    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        params = {
            "exchange": self.primary_exchange,
            "symbol": f"{symbol}-USDT-SWAP",
            "range": "24h"
        }
        return self._request("api/futures/liquidation/heatmap/model2", params, allow_backup=True)

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
            logger.warning(f"清算矩阵数据不完整: y_axis={bool(y_axis)}, liq_data={bool(liq_data)}")
            return result

        if not isinstance(liq_data, list):
            logger.warning("liquidation_leverage_data 不是列表")
            return result

        if len(liq_data) == 0:
            logger.info("清算数据列表为空，可能当前时间段无显著清算压力")
            return result

        total_long = 0.0
        total_short = 0.0
        pain_map = {}

        for item in liq_data:
            if not isinstance(item, list) or len(item) < 3:
                continue
            x_idx = int(item[0])
            y_idx = int(item[1])
            intensity = float(item[2])

            if y_idx < 0 or y_idx >= len(y_axis):
                continue

            price = float(y_axis[y_idx])

            if x_idx == 0:
                if price < current_price:
                    total_long += intensity
            elif x_idx == 1:
                if price > current_price:
                    total_short += intensity
            else:
                continue

            pain_map[price] = pain_map.get(price, 0.0) + intensity

        if total_long == 0 and total_short == 0:
            logger.info("清算解析结果为零，可能当前价格附近无显著清算堆积")
        else:
            logger.info(f"清算解析: 上方空头={total_short:,.0f}, 下方多头={total_long:,.0f}")

        max_pain_price = None
        max_pain_value = 0.0
        for price, val in pain_map.items():
            if val > max_pain_value:
                max_pain_value = val
                max_pain_price = price

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
        if max_pain_price is not None and max_pain_value > 0:
            result["max_pain_price"] = f"{max_pain_price:.1f}"
        if nearest_cluster_price is not None:
            direction = "上" if nearest_cluster_price > current_price else "下"
            intensity_val = pain_map.get(nearest_cluster_price, 0)
            intensity = min(5, int(intensity_val / 5000000) + 1) if intensity_val > 0 else 1
            result["nearest_cluster"] = {
                "direction": direction,
                "price": f"{nearest_cluster_price:.1f}",
                "intensity": str(intensity)
            }

        return result

    def get_open_interest_history(self, symbol: str = "BTC"):
        params = {"exchange": self.primary_exchange, "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/open-interest/history", params, allow_backup=True)

    def get_funding_rate_history(self, symbol: str = "BTC"):
        params = {"exchange": self.primary_exchange, "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 1}
        return self._request("api/futures/funding-rate/history", params, allow_backup=True)

    def get_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {"exchange": self.primary_exchange, "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/global-long-short-account-ratio/history", params, allow_backup=True)

    def get_top_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {"exchange": self.primary_exchange, "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/top-long-short-account-ratio/history", params, allow_backup=False)

    def get_options_info(self, symbol: str = "BTC"):
        params = {"exchange": "Deribit", "symbol": symbol.upper()}
        return self._request("api/option/info", params, allow_backup=False)

    def get_taker_volume_history(self, symbol: str = "BTC"):
        params = {"exchange": self.primary_exchange, "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        # 修正：使用正确的端点，去掉 /v2
        return self._request("api/futures/taker-buy-sell-volume/history", params, allow_backup=True)

    def get_option_max_pain(self, symbol: str = "BTC"):
        params = {"exchange": "Deribit", "symbol": symbol.upper()}
        return self._request("api/option/max-pain", params, allow_backup=False)

    def get_cvd_history(self, symbol: str = "BTC"):
        params = {"exchange": self.primary_exchange, "symbol": f"{symbol}-USDT-SWAP", "interval": "5m", "limit": 24}
        return self._request("api/futures/cvd/history", params, allow_backup=True)

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

    def calculate_volatility_factor(self, symbol: str = "BTC") -> float:
        """计算波动率因子，当前返回默认值1.0，可根据需要扩展"""
        return 1.0

    def get_all_data(self, symbol: str = "BTC", current_price: float = None) -> dict:
        if current_price is None:
            current_price = 70000.0

        data = {}

        # 1. 清算热力图
        heatmap_raw = self.get_liquidation_heatmap(symbol)
        liq_data = self._parse_liquidation_matrix(heatmap_raw, current_price)
        data.update(liq_data)

        # 2. 持仓量24h变化
        oi_history = self.get_open_interest_history(symbol)
        if not isinstance(oi_history, list) or len(oi_history) < 2:
            raise RuntimeError("持仓量数据不足，无法计算24h变化")
        last_close = self._get_close_from_candle(oi_history[-1])
        prev_close = self._get_close_from_candle(oi_history[-2])
        if prev_close <= 0:
            raise RuntimeError("持仓量数据异常，前值非正")
        oi_change = f"{((last_close - prev_close) / prev_close * 100):.2f}%"
        data["oi_change_24h"] = oi_change

        # 3. 资金费率
        funding_history = self.get_funding_rate_history(symbol)
        if not isinstance(funding_history, list) or len(funding_history) == 0:
            raise RuntimeError("资金费率数据为空")
        funding_rate = self._get_close_from_candle(funding_history[-1])
        data["funding_rate"] = funding_rate

        # 4. 全局多空比
        ls_history = self.get_long_short_ratio_history(symbol)
        if not isinstance(ls_history, list) or len(ls_history) == 0:
            raise RuntimeError("全局多空比数据为空")
        ls_ratio = self._get_close_from_candle(ls_history[-1])
        data["long_short_ratio"] = ls_ratio

        # 5. 顶级交易员多空比（仅 BTC 和 ETH 强制要求，其他币种跳过）
        if symbol.upper() in ("BTC", "ETH"):
            top_ls_history = self.get_top_long_short_ratio_history(symbol)
            if not isinstance(top_ls_history, list) or len(top_ls_history) == 0:
                raise RuntimeError("顶级交易员多空比数据为空")
            latest = top_ls_history[-1]
            if isinstance(latest, dict):
                top_ls_ratio = latest.get("top_account_long_short_ratio")
                if top_ls_ratio is None:
                    raise RuntimeError("顶级交易员多空比字段缺失")
            else:
                raise RuntimeError("顶级交易员多空比数据格式异常")
            data["top_long_short_ratio"] = top_ls_ratio
        else:
            logger.info(f"顶级交易员多空比接口不支持 {symbol}，将跳过此数据项")
            data["top_long_short_ratio"] = "N/A"

        # 6. 期权信息（SOL 容错）
        try:
            options_info = self.get_options_info(symbol)
            if not isinstance(options_info, list) or len(options_info) == 0:
                raise RuntimeError("期权信息数据为空")
            first = options_info[0]
            if not isinstance(first, dict):
                raise RuntimeError("期权信息数据格式异常")
            oi_usd = first.get("open_interest_usd")
            if oi_usd is None:
                raise RuntimeError("期权持仓价值字段缺失")
            data["option_oi_usd"] = oi_usd
        except RuntimeError as e:
            if symbol.upper() == "SOL":
                logger.warning(f"SOL 期权信息获取失败: {e}，将跳过此数据项")
                data["option_oi_usd"] = "N/A"
            else:
                raise
        data["put_call_ratio"] = "N/A"
        data["implied_volatility"] = "N/A"

        # 7. 主动吃单比率（所有币种强制要求，失败即报错）
        taker_history = self.get_taker_volume_history(symbol)
        if not isinstance(taker_history, list) or len(taker_history) == 0:
            raise RuntimeError("主动买卖量数据为空")
        buy_vol, sell_vol = self._get_buy_sell_volumes(taker_history[-1])
        total = buy_vol + sell_vol
        if total <= 0:
            raise RuntimeError("主动买卖量数据无效")
        taker_ratio = f"{(buy_vol / total):.2f}"
        data["taker_ratio"] = taker_ratio

        # 8. 期权最大痛点（SOL 容错）
        try:
            max_pain_data = self.get_option_max_pain(symbol)
            skew_value = None
            if isinstance(max_pain_data, dict):
                skew_value = max_pain_data.get("maxPain", max_pain_data.get("max_pain"))
            elif isinstance(max_pain_data, list) and len(max_pain_data) > 0:
                latest = max_pain_data[-1]
                if isinstance(latest, dict):
                    skew_value = latest.get("maxPain", latest.get("max_pain"))
            if skew_value is None:
                raise RuntimeError("期权最大痛点数据缺失")
            data["skew"] = skew_value
        except RuntimeError as e:
            if symbol.upper() == "SOL":
                logger.warning(f"SOL 期权最大痛点获取失败: {e}，将跳过此数据项")
                data["skew"] = "N/A"
            else:
                raise

        # 9. CVD 斜率信号
        cvd_history = self.get_cvd_history(symbol)
        if not isinstance(cvd_history, list) or len(cvd_history) < 12:
            raise RuntimeError("CVD 数据不足，无法计算斜率")
        recent = cvd_history[-12:]
        values = []
        for item in recent:
            if isinstance(item, list) and len(item) >= 5:
                values.append(float(item[4]))
            elif isinstance(item, dict):
                values.append(float(item.get("close", 0)))
            else:
                raise RuntimeError("CVD 数据格式异常")
        if len(values) < 2:
            raise RuntimeError("CVD 有效数据点不足")
        n = len(values)
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        numerator = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator == 0:
            raise RuntimeError("CVD 斜率计算分母为零")
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
