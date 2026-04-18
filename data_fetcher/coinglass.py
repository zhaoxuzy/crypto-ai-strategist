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
        self._prev_liq_data = {}

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

    # ---------- 清算热力图 ----------
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
        x_idx_samples = set()

        for item in liq_data:
            if not isinstance(item, list) or len(item) < 3:
                continue
            x_idx = int(item[0])
            y_idx = int(item[1])
            intensity = float(item[2])
            x_idx_samples.add(x_idx)

            if y_idx < 0 or y_idx >= len(y_axis):
                continue

            price = float(y_axis[y_idx])

            # 修正：根据价格直接归类，不再依赖不可靠的 x_idx
            if price < current_price:
                total_long += intensity
            elif price > current_price:
                total_short += intensity

            pain_map[price] = pain_map.get(price, 0.0) + intensity

        if total_long == 0 and total_short == 0:
            logger.info("清算解析结果为零，可能当前价格附近无显著清算堆积")
            self._liq_zero_count += 1
        else:
            logger.info(f"清算解析: 上方空头={total_short:,.0f}, 下方多头={total_long:,.0f}, x_idx样本={x_idx_samples}")
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

    def _calculate_liq_dynamics(self, curr: dict, prev: dict) -> list:
        signals = []

        total = curr["above"] + curr["below"]
        if total > 0:
            ratio = curr["above"] / total
            if ratio > 0.65:
                signals.append(f"清算压力偏空({ratio:.1%})")
            elif ratio < 0.35:
                signals.append(f"清算压力偏多({1-ratio:.1%})")

        if prev and curr["max_pain"] > 0 and prev.get("max_pain", 0) > 0:
            if curr["max_pain"] > prev["max_pain"] * 1.002:
                signals.append("最大痛点上移↑")
            elif curr["max_pain"] < prev["max_pain"] * 0.998:
                signals.append("最大痛点下移↓")

        cluster_price = curr["cluster_price"]
        atr = curr.get("atr", 1)
        if cluster_price > 0 and atr > 0:
            distance_atr = abs(curr["current_price"] - cluster_price) / atr
            if distance_atr < 0.5 and curr["cluster_intensity"] >= 4:
                signals.append(f"强磁吸区(距{cluster_price:.0f}, 强度{curr['cluster_intensity']})")

        if prev:
            prev_total = prev.get("above", 0) + prev.get("below", 0)
            curr_total = curr["above"] + curr["below"]
            if prev_total > 0 and curr_total > 0:
                change_pct = (curr_total - prev_total) / prev_total
                if change_pct > 0.3:
                    signals.append(f"清算堆积加速↑({change_pct:.0%})")
                elif change_pct < -0.3:
                    signals.append(f"清算堆积衰减↓({-change_pct:.0%})")

        return signals

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

    # ---------- 主动买卖量 ----------
    def get_taker_volume_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/taker-buy-sell-volume/history", params, allow_backup=True)

    # ---------- 期权最大痛点 ----------
    def get_option_max_pain(self, symbol: str = "BTC"):
        params = {"exchange": "Deribit", "symbol": symbol.upper()}
        return self._request("api/option/max-pain", params, allow_backup=False, silent_fail=True)

    # ---------- CVD ----------
    def get_cvd_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1m", "limit": 60}
        return self._request("api/futures/cvd/history", params, allow_backup=True, silent_fail=True)

    # ---------- 净持仓 ----------
    def get_net_position_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/v2/net-position/history", params, allow_backup=True, silent_fail=True)

    # ---------- 累计资金费率 ----------
    def get_accumulated_funding_rate(self, symbol: str = "BTC"):
        params = {"symbol": symbol.upper(), "range": "24h"}
        return self._request("api/futures/funding-rate/accumulated-exchange-list", params, allow_backup=False, silent_fail=True)

    # ---------- 聚合主动买卖 ----------
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

       # ---------- ETH/BTC 汇率 ----------
    def get_eth_btc_ratio(self) -> dict:
        """
        获取 ETH/BTC 汇率及其趋势。
        返回 dict: {"current_ratio": 当前汇率, "ma_4h": 4小时均线, "trend": "up"/"down"/"neutral"}
        """
        try:
            params = {"exchange": "Binance", "symbol": "ETHUSDT", "interval": "1h", "limit": 5}
            eth_data = self._request("api/spot/price/history", params, allow_backup=False, silent_fail=True)
            params["symbol"] = "BTCUSDT"
            btc_data = self._request("api/spot/price/history", params, allow_backup=False, silent_fail=True)
            
            # 校验数据格式与长度
            if not isinstance(eth_data, list) or not isinstance(btc_data, list):
                logger.warning("ETH/BTC 汇率数据格式异常，返回默认值")
                return {"current_ratio": 0.0, "ma_4h": 0.0, "trend": "neutral"}

            if len(eth_data) < 4 or len(btc_data) < 4:
                logger.warning(f"ETH/BTC 汇率数据不足，ETH数据量:{len(eth_data)}，BTC数据量:{len(btc_data)}")
                return {"current_ratio": 0.0, "ma_4h": 0.0, "trend": "neutral"}

            # 提取收盘价（兼容 list 和 dict 两种格式）
            eth_close_4 = []
            for k in eth_data[-4:]:
                if isinstance(k, list) and len(k) >= 5:
                    eth_close_4.append(float(k[4]))
                elif isinstance(k, dict):
                    eth_close_4.append(float(k.get("close", 0)))
            btc_close_4 = []
            for k in btc_data[-4:]:
                if isinstance(k, list) and len(k) >= 5:
                    btc_close_4.append(float(k[4]))
                elif isinstance(k, dict):
                    btc_close_4.append(float(k.get("close", 0)))

            if len(eth_close_4) < 4 or len(btc_close_4) < 4:
                logger.warning("ETH/BTC 有效收盘价不足4个")
                return {"current_ratio": 0.0, "ma_4h": 0.0, "trend": "neutral"}

            eth_ma = sum(eth_close_4) / 4
            btc_ma = sum(btc_close_4) / 4
            ma_4h_ratio = eth_ma / btc_ma if btc_ma > 0 else 0.0

            current_ratio = eth_close_4[-1] / btc_close_4[-1] if btc_close_4[-1] > 0 else 0.0

            trend = "up" if current_ratio > ma_4h_ratio else "down"

            logger.info(f"ETH/BTC 汇率: 当前={current_ratio:.6f}, MA4H={ma_4h_ratio:.6f}, 趋势={trend}")
            return {"current_ratio": round(current_ratio, 6), "ma_4h": round(ma_4h_ratio, 6), "trend": trend}

        except Exception as e:
            logger.warning(f"获取 ETH/BTC 汇率失败: {e}")
            return {"current_ratio": 0.0, "ma_4h": 0.0, "trend": "neutral"}

    # ---------- 交易所钱包余额 ----------
    def get_exchange_balances(self) -> dict:
        try:
            btc_data = self._request("api/exchange/balance/list", {"symbol": "BTC"}, allow_backup=False, silent_fail=True)
            if not isinstance(btc_data, list) or len(btc_data) == 0:
                return {"btc_flow": "neutral", "stable_flow": "neutral"}
            
            stable_data = self._request("api/exchange/balance/list", {"symbol": "USDT(ETH)"}, allow_backup=False, silent_fail=True)
            
            btc_total_change = 0.0
            stable_total_change = 0.0
            
            for ex in btc_data:
                btc_total_change += float(ex.get("balance_change_1d", 0))
            
            if isinstance(stable_data, list):
                for ex in stable_data:
                    stable_total_change += float(ex.get("balance_change_1d", 0))
            
            btc_flow = "in" if btc_total_change > 10 else ("out" if btc_total_change < -10 else "neutral")
            stable_flow = "in" if stable_total_change > 1000000 else ("out" if stable_total_change < -1000000 else "neutral")
            
            logger.info(f"交易所余额: BTC净变动={btc_total_change:.0f} ({btc_flow}), 稳定币净变动={stable_total_change:.0f} ({stable_flow})")
            return {"btc_flow": btc_flow, "stable_flow": stable_flow, "btc_change": btc_total_change, "stable_change": stable_total_change}
        except Exception as e:
            logger.warning(f"获取交易所余额失败: {e}")
            return {"btc_flow": "neutral", "stable_flow": "neutral", "btc_change": 0.0, "stable_change": 0.0}

    # ---------- 因子一：恐惧贪婪指数 ----------
    def get_fear_greed_index(self) -> dict:
        try:
            import requests
            logger.info("尝试从 alternative.me 获取恐惧贪婪指数...")
            resp = requests.get("https://api.alternative.me/fng/?limit=2", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"alternative.me 响应数据: {data}")
                if data.get("data") and len(data["data"]) >= 2:
                    current = int(data["data"][0]["value"])
                    prev = int(data["data"][1]["value"])
                    classification = data["data"][0]["value_classification"]
                    logger.info(f"✅ 恐惧贪婪指数 (alternative.me): 当前={current}, 昨日={prev}")
                    return {"current": current, "prev": prev, "classification": classification}
                elif data.get("data") and len(data["data"]) == 1:
                    current = int(data["data"][0]["value"])
                    classification = data["data"][0]["value_classification"]
                    logger.info(f"✅ 恐惧贪婪指数 (alternative.me): 当前={current}, 昨日无数据")
                    return {"current": current, "prev": current, "classification": classification}
                else:
                    logger.warning(f"alternative.me 返回数据格式异常: {data}")
            else:
                logger.warning(f"alternative.me 请求失败，状态码: {resp.status_code}")
        except Exception as e:
            logger.warning(f"alternative.me 请求异常: {e}")

        try:
            logger.info("尝试从 CoinGlass 获取恐惧贪婪指数...")
            cg_data = self._request("api/index/fear-greed-history", {}, allow_backup=False, silent_fail=True)
            logger.info(f"CoinGlass 恐惧贪婪原始响应: {cg_data}")
            if cg_data and isinstance(cg_data, list) and len(cg_data) >= 2:
                current = int(cg_data[0].get("value", 50))
                prev = int(cg_data[1].get("value", 50))
                classification = cg_data[0].get("value_classification", "Neutral")
                logger.info(f"✅ 恐惧贪婪指数 (CoinGlass): 当前={current}, 昨日={prev}")
                return {"current": current, "prev": prev, "classification": classification}
            else:
                logger.warning("CoinGlass 恐惧贪婪数据不足或格式错误")
        except Exception as e:
            logger.warning(f"CoinGlass 恐惧贪婪请求异常: {e}")

        logger.error("❌ 所有恐惧贪婪指数数据源均失败，使用默认值 50")
        return {"current": 50, "prev": 50, "classification": "Neutral", "error": True}

       # ---------- 因子二：Coinbase 溢价指数 ----------
    def get_coinbase_premium(self, btc_price: float = None) -> dict:
        """
        获取 Coinbase 溢价指数（Coinbase Pro 与 Binance 的 BTC 价差）
        返回 dict: {"premium_pct": 溢价百分比, "premium_usd": 溢价美元值}
        """
        try:
            params = {"interval": "1h"}
            logger.info("尝试获取 Coinbase 溢价指数...")
            data = self._request("api/coinbase-premium-index", params, allow_backup=False, silent_fail=True)
            if not data:
                return {"premium_pct": 0.0, "premium_usd": 0.0}
            
            # 提取最新一条数据
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]
            elif isinstance(data, dict):
                latest = data
            else:
                return {"premium_pct": 0.0, "premium_usd": 0.0}
            
            # CoinGlass 返回的 premium_rate 字段就是百分比（例如 0.0035 表示 0.35%）
            if isinstance(latest, dict):
                premium_rate = float(latest.get("premium_rate", 0))
                premium_usd = float(latest.get("premium", 0))
                # premium_rate 已经是小数形式的百分比，直接乘以 100 得到百分数
                premium_pct = premium_rate * 100
                logger.info(f"✅ Coinbase 溢价: {premium_pct:.4f}% (原始rate: {premium_rate}, usd: {premium_usd})")
                return {"premium_pct": premium_pct, "premium_usd": premium_usd}
            else:
                return {"premium_pct": 0.0, "premium_usd": 0.0}
        except Exception as e:
            logger.warning(f"获取 Coinbase 溢价指数失败: {e}")
            return {"premium_pct": 0.0, "premium_usd": 0.0, "error": True}

    # ---------- 因子三：稳定币市值变化 ----------
    def get_stablecoin_market_cap_change(self) -> dict:
        try:
            logger.info("尝试获取稳定币市值变化...")
            data = self._request("api/index/stableCoin-marketCap-history", {}, allow_backup=False, silent_fail=True)
            logger.info(f"稳定币市值原始响应类型: {type(data)}")
            if not data:
                logger.warning("稳定币市值返回空数据")
                return {"change_7d": 0.0, "current_mcap": 0.0, "error": True}
            
            data_list = data.get("data_list", [])
            if not data_list:
                if isinstance(data, list):
                    data_list = data
                else:
                    logger.warning("稳定币市值数据格式未知")
                    return {"change_7d": 0.0, "current_mcap": 0.0, "error": True}
            
            if len(data_list) < 7:
                logger.warning(f"稳定币市值数据不足7条，实际 {len(data_list)} 条")
                return {"change_7d": 0.0, "current_mcap": 0.0, "error": True}
            
            def get_total_mcap(item):
                if isinstance(item, dict):
                    return sum(float(v) for v in item.values())
                return 0.0
            
            current = get_total_mcap(data_list[-1])
            prev_index = -8 if len(data_list) >= 8 else 0
            prev_7d = get_total_mcap(data_list[prev_index])
            
            change_7d = ((current - prev_7d) / prev_7d * 100) if prev_7d > 0 else 0.0
            logger.info(f"✅ 稳定币市值变化: {change_7d:.2f}% (当前: {current:.0f}, 7日前: {prev_7d:.0f})")
            return {"change_7d": change_7d, "current_mcap": current}
        except Exception as e:
            logger.warning(f"获取稳定币市值变化失败: {e}")
            return {"change_7d": 0.0, "current_mcap": 0.0, "error": True}

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

    def get_all_data(self, symbol: str = "BTC", current_price: float = None, atr: float = None) -> dict:
        if current_price is None:
            current_price = 70000.0

        data = {}

        heatmap_raw = self.get_liquidation_heatmap(symbol)
        liq_data = self._parse_liquidation_matrix(heatmap_raw, current_price)
        data.update(liq_data)

        prev = self._prev_liq_data.get(symbol, {})
        curr = {
            "above": float(str(data.get("above_short_liquidation", "0")).replace(",", "")),
            "below": float(str(data.get("below_long_liquidation", "0")).replace(",", "")),
            "max_pain": float(data.get("max_pain_price", 0)) if data.get("max_pain_price") != "N/A" else 0,
            "cluster_price": float(data["nearest_cluster"]["price"]) if data["nearest_cluster"]["price"] != "N/A" else 0,
            "cluster_intensity": int(data["nearest_cluster"]["intensity"]) if data["nearest_cluster"]["intensity"] != "N/A" else 0,
            "current_price": current_price,
            "atr": atr if atr else 1.0
        }
        dynamic_signals = self._calculate_liq_dynamics(curr, prev)
        data["liq_dynamic_signals"] = dynamic_signals
        self._prev_liq_data[symbol] = curr

        oi_history = self.get_open_interest_history(symbol)
        if not isinstance(oi_history, list) or len(oi_history) < 2:
            raise RuntimeError("持仓量数据不足，无法计算24h变化")
        last_close = self._get_close_from_candle(oi_history[-1])
        prev_close = self._get_close_from_candle(oi_history[-2])
        if prev_close <= 0:
            raise RuntimeError("持仓量数据异常，前值非正")
        oi_change = f"{((last_close - prev_close) / prev_close * 100):.2f}%"
        data["oi_change_24h"] = oi_change

        funding_history = self.get_funding_rate_history(symbol)
        if not isinstance(funding_history, list) or len(funding_history) == 0:
            raise RuntimeError("资金费率数据为空")
        funding_rate = self._get_close_from_candle(funding_history[-1])
        data["funding_rate"] = funding_rate

        ls_history = self.get_long_short_ratio_history(symbol)
        if not isinstance(ls_history, list) or len(ls_history) == 0:
            raise RuntimeError("全局多空比数据为空")
        ls_ratio = self._get_close_from_candle(ls_history[-1])
        data["long_short_ratio"] = ls_ratio
        data["ls_account_ratio"] = ls_ratio

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

        taker_history = self.get_taker_volume_history(symbol)
        if not isinstance(taker_history, list) or len(taker_history) == 0:
            raise RuntimeError("主动买卖量数据为空")
        buy_vol, sell_vol = self._get_buy_sell_volumes(taker_history[-1])
        total = buy_vol + sell_vol
        if total <= 0:
            raise RuntimeError("主动买卖量数据无效")
        taker_ratio = f"{(buy_vol / total):.2f}"
        data["taker_ratio"] = taker_ratio

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

        orderbook_data = self.get_orderbook_imbalance(symbol)
        data["orderbook_imbalance"] = orderbook_data.get("imbalance", 0.0)
        data["orderbook_bids_usd"] = orderbook_data.get("bids_usd", 0.0)
        data["orderbook_asks_usd"] = orderbook_data.get("asks_usd", 0.0)

        data["eth_btc_ratio"] = self.get_eth_btc_ratio()

        return data
