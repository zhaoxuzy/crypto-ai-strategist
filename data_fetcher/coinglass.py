import os
import time
import requests
from utils.logger import logger

class CoinGlassClient:
    def __init__(self):
        self.api_key = os.getenv("COINGLASS_API_KEY", "")
        self.base_url = "https://www.keystore.com.cn/api/v1/proxy/coinglass/v4"
        self.delay = 6.0
        self.primary_exchange = "OKX"
        self.backup_exchanges = ["Bybit"]
        self._liq_zero_count = 0
        self._use_model1_fallback = False

    def _request(self, endpoint: str, params: dict = None, max_retries: int = 3, allow_backup: bool = True, silent_fail: bool = False) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "accept": "application/json",
            "X-Api-Key": self.api_key
        }
        base_params = params.copy() if params else {}
        
        if allow_backup:
            exchanges_to_try = [self.primary_exchange] + self.backup_exchanges
        else:
            exchanges_to_try = [base_params.get("exchange", self.primary_exchange)]

        last_error = None

        for exchange in exchanges_to_try:
            current_params = base_params.copy()
            if "exchange" in current_params and allow_backup:
                current_params["exchange"] = exchange
            elif "exchange" not in current_params and allow_backup:
                current_params["exchange"] = exchange

            for attempt in range(max_retries):
                try:
                    logger.info(f"请求 CoinGlass: {endpoint} | exchange={current_params.get('exchange', 'N/A')} | params={current_params}" + 
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
                            if "rate limit" in str(msg).lower():
                                wait_time = 10 * (attempt + 1)
                            else:
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

        if silent_fail:
            logger.warning(f"CoinGlass 数据获取失败（静默）: {last_error}")
            return {}
        raise RuntimeError(f"CoinGlass 数据获取失败，所有尝试均无效。最后错误: {last_error}")

    # ---------- 清算热力图（主用 model2，备用 model1）----------
    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        params = {
            "exchange": "OKX",
            "symbol": f"{symbol}-USDT-SWAP",
            "range": "3d"
        }
        data = self._request("api/futures/liquidation/heatmap/model2", params, allow_backup=True, silent_fail=True)
        if data and data.get("liquidation_leverage_data"):
            self._use_model1_fallback = False
            return data

        logger.warning("model2 返回空数据，尝试 model1 备用")
        params["range"] = "24h"
        data = self._request("api/futures/liquidation/heatmap/model1", params, allow_backup=True, silent_fail=True)
        if data and data.get("liquidation_leverage_data"):
            self._use_model1_fallback = True
            return data

        return {}

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
            self._liq_zero_count += 1
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

            pain_map[price] = pain_map.get(price, 0.0) + intensity

        if total_long == 0 and total_short == 0:
            logger.info("清算解析结果为零，可能当前价格附近无显著清算堆积")
            self._liq_zero_count += 1
        else:
            logger.info(f"清算解析: 上方空头={total_short:,.0f}, 下方多头={total_long:,.0f}")
            self._liq_zero_count = 0

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

    def get_liq_zero_count(self) -> int:
        return self._liq_zero_count

    def get_liq_zero_warning(self) -> str:
        if self._liq_zero_count >= 2:
            return "⚠️ 系统告警：连续两次未获取到有效清算数据，已启用备用模型。"
        return ""

    def get_data_source_status(self) -> str:
        if self._use_model1_fallback:
            return "清算数据源：model1（备用）"
        return "清算数据源：model2（主用）"

    # ---------- 持仓量 ----------
    def get_open_interest_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/open-interest/history", params, allow_backup=True)

    # ---------- 资金费率 ----------
    def get_funding_rate_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 1}
        return self._request("api/futures/funding-rate/history", params, allow_backup=True)

    # ---------- 全局多空比 ----------
    def get_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/global-long-short-account-ratio/history", params, allow_backup=True)

    # ---------- 顶级交易员多空比 ----------
    def get_top_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/top-long-short-account-ratio/history", params, allow_backup=False)

    # ---------- 期权信息 ----------
    def get_options_info(self, symbol: str = "BTC"):
        params = {"exchange": "Deribit", "symbol": symbol.upper()}
        return self._request("api/option/info", params, allow_backup=False, silent_fail=True)

    # ---------- 主动买卖量（单交易所）----------
    def get_taker_volume_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/taker-buy-sell-volume/history", params, allow_backup=True)

    # ---------- 期权最大痛点 ----------
    def get_option_max_pain(self, symbol: str = "BTC"):
        params = {"exchange": "Deribit", "symbol": symbol.upper()}
        return self._request("api/option/max-pain", params, allow_backup=False, silent_fail=True)

    # ---------- CVD（粒度1分钟）----------
    def get_cvd_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1m", "limit": 60}
        return self._request("api/futures/cvd/history", params, allow_backup=True, silent_fail=True)

    # ---------- 净多净空持仓 v2 ----------
    def get_net_position_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/v2/net-position/history", params, allow_backup=True, silent_fail=True)

    # ---------- 累计资金费率 ----------
    def get_accumulated_funding_rate(self, symbol: str = "BTC"):
        params = {"symbol": symbol.upper(), "range": "24h"}
        return self._request("api/futures/funding-rate/accumulated-exchange-list", params, allow_backup=False, silent_fail=True)

    # ---------- 聚合主动买卖历史 ----------
    def get_aggregated_taker_volume(self, symbol: str = "BTC"):
        params = {"symbol": symbol.upper(), "interval": "1h", "limit": 24, "exchange_list": "OKX"}
        return self._request("api/futures/aggregated-taker-buy-sell-volume/history", params, allow_backup=False, silent_fail=True)

    # ---------- 订单簿失衡率 ----------
    def get_orderbook_imbalance(self, symbol: str = "BTC") -> dict:
        try:
            params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1m", "limit": 1}
            data = self._request("api/futures/orderbook/ask-bids-history", params, allow_backup=True, silent_fail=True)
            if isinstance(data, list) and len(data) > 0:
                latest = data[0]
                bids_usd = float(latest.get("bids_usd", 0))
                asks_usd = float(latest.get("asks_usd", 0))
                total = bids_usd + asks_usd
                if total > 0:
                    imbalance = (bids_usd - asks_usd) / total
                    return {"imbalance": round(imbalance, 4), "bids_usd": bids_usd, "asks_usd": asks_usd}
            return {"imbalance": 0.0, "bids_usd": 0.0, "asks_usd": 0.0}
        except Exception as e:
            logger.warning(f"获取订单簿失衡率失败: {e}")
            return {"imbalance": 0.0, "bids_usd": 0.0, "asks_usd": 0.0}

    @staticmethod
    def _get_close_from_candle(candle) -> float:
        if isinstance(candle, list) and len(candle) >= 5:
            return float(candle[4])
        elif isinstance(candle, dict):
            return float(candle.get("close", 0))
        return 0.0

    @staticmethod
    def _get_buy_sell_volumes(candle):
        if isinstance(candle, dict):
            buy = float(candle.get("taker_buy_volume_usd", 0))
            sell = float(candle.get("taker_sell_volume_usd", 0))
            return buy, sell
        return 0.0, 0.0

    @staticmethod
    def _get_aggregated_buy_sell_volumes(candle):
        if isinstance(candle, dict):
            buy = float(candle.get("aggregated_buy_volume_usd", 0))
            sell = float(candle.get("aggregated_sell_volume_usd", 0))
            return buy, sell
        return 0.0, 0.0

    def calculate_volatility_factor(self, symbol: str = "BTC") -> float:
        return 1.0

    def get_market_regime_from_klines(self, klines: list, current_price: float, atr: float) -> dict:
        """基于已获取的K线数据计算市场状态，无需额外API请求"""
        if not klines or len(klines) < 20:
            return {"regime": "range", "details": {"reason": "K线数据不足，默认震荡市"}}

        closes = []
        highs = []
        lows = []
        for k in klines:
            if len(k) >= 5:
                closes.append(float(k[4]))
                highs.append(float(k[2]))
                lows.append(float(k[3]))

        if len(closes) < 20:
            return {"regime": "range", "details": {"reason": "有效K线不足"}}

        ma20 = sum(closes[-20:]) / 20
        price_deviation = (current_price - ma20) / ma20

        true_ranges = []
        for i in range(1, len(highs)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            true_ranges.append(tr)
        if len(true_ranges) >= 14:
            historical_atr = sum(true_ranges[-14:]) / 14
        else:
            historical_atr = atr

        volatility_ratio = atr / historical_atr if historical_atr > 0 else 1.0

        from data_fetcher.macro_cache import get_macro_data
        macro = get_macro_data()
        fg_value = int(macro.get("fear_greed", {}).get("value", 50))

        if fg_value < 25 or fg_value > 75:
            regine = "extreme"
            reason = f"恐惧贪婪指数{fg_value}处于极端区域"
        elif abs(price_deviation) > 0.03 and volatility_ratio > 1.3:
            regine = "trend"
            reason = f"价格偏离MA20 {price_deviation*100:.1f}%，波动率比值 {volatility_ratio:.2f}"
        else:
            regine = "range"
            reason = "价格在均线附近，波动率正常"

        return {
            "regime": regine,
            "details": {
                "reason": reason,
                "price_deviation": round(price_deviation, 4),
                "volatility_ratio": round(volatility_ratio, 2),
                "fg_value": fg_value
            }
        }

    def get_all_data(self, symbol: str = "BTC", current_price: float = None, atr: float = None) -> dict:
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
        data["ls_account_ratio"] = ls_ratio

        # 5. 顶级交易员多空比
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

        # 6. 期权信息
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

        # 7. 主动吃单比率（单交易所）
        taker_history = self.get_taker_volume_history(symbol)
        if not isinstance(taker_history, list) or len(taker_history) == 0:
            raise RuntimeError("主动买卖量数据为空")
        buy_vol, sell_vol = self._get_buy_sell_volumes(taker_history[-1])
        total = buy_vol + sell_vol
        if total <= 0:
            raise RuntimeError("主动买卖量数据无效")
        taker_ratio = f"{(buy_vol / total):.2f}"
        data["taker_ratio"] = taker_ratio

        # 8. 期权最大痛点
        try:
            max_pain_data = self.get_option_max_pain(symbol)
            skew_value = None
            if isinstance(max_pain_data, list) and len(max_pain_data) > 0:
                latest = max_pain_data[-1]
                if isinstance(latest, dict):
                    skew_value = latest.get("max_pain_price")
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
        if not isinstance(cvd_history, list) or len(cvd_history) < 30:
            raise RuntimeError("CVD 数据不足，无法计算斜率")
        recent = cvd_history[-30:]
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

        # 10. 净多净空持仓
        try:
            net_pos_history = self.get_net_position_history(symbol)
            if not isinstance(net_pos_history, list) or len(net_pos_history) == 0:
                raise RuntimeError("净持仓数据为空")
            latest = net_pos_history[-1]
            if isinstance(latest, dict):
                net_position_cum = latest.get("net_position_change_cum")
                if net_position_cum is None:
                    raise RuntimeError("净持仓累积字段缺失")
                data["net_position_cum"] = round(float(net_position_cum), 2)
            else:
                raise RuntimeError("净持仓数据格式异常")
        except RuntimeError as e:
            if symbol.upper() == "SOL":
                logger.warning(f"SOL 净持仓数据获取失败: {e}，将跳过")
                data["net_position_cum"] = "N/A"
            else:
                raise

        # 11. 累计资金费率
        try:
            acc_funding = self.get_accumulated_funding_rate(symbol)
            okx_funding = "N/A"
            if isinstance(acc_funding, list) and len(acc_funding) > 0:
                for item in acc_funding:
                    if isinstance(item, dict) and item.get("symbol") == symbol.upper():
                        stable_list = item.get("stablecoin_margin_list", [])
                        for ex in stable_list:
                            if ex.get("exchange") == "OKX":
                                okx_funding = ex.get("funding_rate")
                                break
                        break
            data["accumulated_funding_rate"] = okx_funding if okx_funding is not None else "N/A"
        except Exception as e:
            logger.warning(f"累计资金费率获取失败: {e}")
            data["accumulated_funding_rate"] = "N/A"

        # 12. 聚合主动买卖比率
        try:
            agg_taker = self.get_aggregated_taker_volume(symbol)
            if not isinstance(agg_taker, list) or len(agg_taker) == 0:
                raise RuntimeError("聚合主动买卖数据为空")
            latest_agg = agg_taker[-1]
            if isinstance(latest_agg, dict):
                buy_agg, sell_agg = self._get_aggregated_buy_sell_volumes(latest_agg)
                total_agg = buy_agg + sell_agg
                if total_agg <= 0:
                    raise RuntimeError("聚合主动买卖数据无效")
                agg_taker_ratio = f"{(buy_agg / total_agg):.2f}"
                data["aggregated_taker_ratio"] = agg_taker_ratio
            else:
                raise RuntimeError("聚合主动买卖数据格式异常")
        except RuntimeError as e:
            if symbol.upper() == "SOL":
                logger.warning(f"SOL 聚合主动买卖数据获取失败: {e}，将跳过")
                data["aggregated_taker_ratio"] = "N/A"
            else:
                raise

        # 13. 订单簿失衡率
        orderbook_data = self.get_orderbook_imbalance(symbol)
        data["orderbook_imbalance"] = orderbook_data.get("imbalance", 0.0)
        data["orderbook_bids_usd"] = orderbook_data.get("bids_usd", 0.0)
        data["orderbook_asks_usd"] = orderbook_data.get("asks_usd", 0.0)

        return data
