import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
from utils.logger import logger

class RateLimiter:
    """简单的速率限制器，确保最小间隔"""
    def __init__(self, min_interval: float = 3.0):
        self.min_interval = min_interval
        self._last_request_time = 0.0

    def wait(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.time()


class CoinGlassClient:
    def __init__(self):
        self.api_key = os.getenv("COINGLASS_API_KEY", "")
        self.base_url = "https://www.keystore.com.cn/api/v1/proxy/coinglass/v4"
        self.primary_exchange = "OKX"
        self.backup_exchanges = ["Bybit"]
        self._liq_zero_count = 0
        self._use_model1_fallback = False
        self._prev_liq_data = {}

        # 速率控制：最多 5 个并发，最小间隔 3 秒
        self._rate_limiter = RateLimiter(min_interval=3.0)
        self._semaphore = Semaphore(5)

    def _request(self, endpoint: str, params: dict = None, max_retries: int = 4, allow_backup: bool = True, silent_fail: bool = False) -> dict:
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
                with self._semaphore:
                    self._rate_limiter.wait()
                    try:
                        logger.info(f"请求 CoinGlass: {endpoint} | exchange={current_params.get('exchange', 'N/A')} | params={current_params}" + 
                                    (f" (重试 {attempt+1}/{max_retries})" if attempt > 0 else ""))
                        resp = requests.get(url, params=current_params, headers=headers, timeout=15)
                        data = resp.json()
                        if data.get("code") in (0, "0"):
                            return data.get("data", {})
                        else:
                            msg = f"CoinGlass API 错误: {data.get('msg', data)}"
                            last_error = msg
                            if attempt < max_retries - 1:
                                if "rate limit" in str(msg).lower():
                                    wait_time = 15 * (attempt + 1)
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

    # ---------- 各 API 方法保持不变 ----------
    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "range": "3d"}
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

    def get_open_interest_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/open-interest/history", params, allow_backup=True)

    def get_funding_rate_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 1}
        return self._request("api/futures/funding-rate/history", params, allow_backup=True)

    def get_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/global-long-short-account-ratio/history", params, allow_backup=True)

    def get_top_long_short_ratio_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/top-long-short-account-ratio/history", params, allow_backup=False)

    def get_options_info(self, symbol: str = "BTC"):
        params = {"exchange": "Deribit", "symbol": symbol.upper()}
        return self._request("api/option/info", params, allow_backup=False, silent_fail=True)

    def get_taker_volume_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/taker-buy-sell-volume/history", params, allow_backup=True)

    def get_option_max_pain(self, symbol: str = "BTC"):
        params = {"exchange": "Deribit", "symbol": symbol.upper()}
        return self._request("api/option/max-pain", params, allow_backup=False, silent_fail=True)

    def get_cvd_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1m", "limit": 60}
        return self._request("api/futures/cvd/history", params, allow_backup=True, silent_fail=True)

    def get_net_position_history(self, symbol: str = "BTC"):
        params = {"exchange": "OKX", "symbol": f"{symbol}-USDT-SWAP", "interval": "1h", "limit": 24}
        return self._request("api/futures/v2/net-position/history", params, allow_backup=True, silent_fail=True)

    def get_accumulated_funding_rate(self, symbol: str = "BTC"):
        params = {"symbol": symbol.upper(), "range": "24h"}
        return self._request("api/futures/funding-rate/accumulated-exchange-list", params, allow_backup=False, silent_fail=True)

    def get_aggregated_taker_volume(self, symbol: str = "BTC"):
        params = {"symbol": symbol.upper(), "interval": "1h", "limit": 24, "exchange_list": "OKX"}
        return self._request("api/futures/aggregated-taker-buy-sell-volume/history", params, allow_backup=False, silent_fail=True)

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

    def get_eth_btc_ratio(self) -> dict:
        try:
            params = {"exchange": "Binance", "symbol": "ETHUSDT", "interval": "1h", "limit": 5}
            eth_data = self._request("api/spot/price/history", params, allow_backup=False, silent_fail=True)
            params["symbol"] = "BTCUSDT"
            btc_data = self._request("api/spot/price/history", params, allow_backup=False, silent_fail=True)
            if not isinstance(eth_data, list) or not isinstance(btc_data, list) or len(eth_data) < 4 or len(btc_data) < 4:
                logger.warning("ETH/BTC 汇率数据不足")
                return {"current_ratio": 0.0, "ma_4h": 0.0, "trend": "neutral"}
            eth_close_4 = [float(k[4]) if isinstance(k, list) and len(k) >= 5 else float(k.get("close", 0)) for k in eth_data[-4:]]
            btc_close_4 = [float(k[4]) if isinstance(k, list) and len(k) >= 5 else float(k.get("close", 0)) for k in btc_data[-4:]]
            if len(eth_close_4) < 4 or len(btc_close_4) < 4:
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

    def get_exchange_balances(self) -> dict:
        try:
            btc_data = self._request("api/exchange/balance/list", {"symbol": "BTC"}, allow_backup=False, silent_fail=True)
            if not isinstance(btc_data, list) or len(btc_data) == 0:
                return {"btc_flow": "neutral", "stable_flow": "neutral"}
            stable_data = self._request("api/exchange/balance/list", {"symbol": "USDT(ETH)"}, allow_backup=False, silent_fail=True)
            btc_total_change = sum(float(ex.get("balance_change_1d", 0)) for ex in btc_data)
            stable_total_change = sum(float(ex.get("balance_change_1d", 0)) for ex in stable_data) if isinstance(stable_data, list) else 0.0
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
            import requests as req
            logger.info("尝试从 alternative.me 获取恐惧贪婪指数...")
            resp = req.get("https://api.alternative.me/fng/?limit=2", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("data") and len(data["data"]) >= 2:
                    current = int(data["data"][0]["value"])
                    prev = int(data["data"][1]["value"])
                    classification = data["data"][0]["value_classification"]
                    logger.info(f"✅ 恐惧贪婪指数 (alternative.me): 当前={current}, 昨日={prev}")
                    return {"current": current, "prev": prev, "classification": classification}
        except Exception as e:
            logger.warning(f"alternative.me 请求异常: {e}")
        logger.error("❌ 所有恐惧贪婪指数数据源均失败，使用默认值 50")
        return {"current": 50, "prev": 50, "classification": "Neutral", "error": True}

    def get_coinbase_premium(self, btc_price: float = None) -> dict:
        try:
            params = {"interval": "1h"}
            data = self._request("api/coinbase-premium-index", params, allow_backup=False, silent_fail=True)
            if not data:
                return {"premium_pct": 0.0, "premium_usd": 0.0}
            latest = data[-1] if isinstance(data, list) else data
            if isinstance(latest, dict):
                premium_rate = float(latest.get("premium_rate", 0))
                premium_usd = float(latest.get("premium", 0))
                premium_pct = premium_rate * 100
                logger.info(f"✅ Coinbase 溢价: {premium_pct:.4f}% (原始rate: {premium_rate}, usd: {premium_usd})")
                return {"premium_pct": premium_pct, "premium_usd": premium_usd}
            return {"premium_pct": 0.0, "premium_usd": 0.0}
        except Exception as e:
            logger.warning(f"获取 Coinbase 溢价指数失败: {e}")
            return {"premium_pct": 0.0, "premium_usd": 0.0, "error": True}

    def get_stablecoin_market_cap_change(self) -> dict:
        try:
            data = self._request("api/index/stableCoin-marketCap-history", {}, allow_backup=False, silent_fail=True)
            if not data:
                return {"change_7d": 0.0, "current_mcap": 0.0, "error": True}
            data_list = data.get("data_list", [])
            if not data_list and isinstance(data, list):
                data_list = data
            if len(data_list) < 7:
                return {"change_7d": 0.0, "current_mcap": 0.0, "error": True}
            def get_total_mcap(item):
                if isinstance(item, dict):
                    return sum(float(v) for v in item.values())
                return 0.0
            current = get_total_mcap(data_list[-1])
            prev_7d = get_total_mcap(data_list[-8] if len(data_list) >= 8 else data_list[0])
            change_7d = ((current - prev_7d) / prev_7d * 100) if prev_7d > 0 else 0.0
            logger.info(f"✅ 稳定币市值变化: {change_7d:.2f}% (当前: {current:.0f}, 7日前: {prev_7d:.0f})")
            return {"change_7d": change_7d, "current_mcap": current}
        except Exception as e:
            logger.warning(f"获取稳定币市值变化失败: {e}")
            return {"change_7d": 0.0, "current_mcap": 0.0, "error": True}

    # ---------- 并行化的 get_all_data ----------
    def get_all_data(self, symbol: str = "BTC", current_price: float = None, atr: float = None) -> dict:
        if current_price is None:
            current_price = 70000.0

        tasks = {
            "heatmap": lambda: self.get_liquidation_heatmap(symbol),
            "oi": lambda: self.get_open_interest_history(symbol),
            "funding": lambda: self.get_funding_rate_history(symbol),
            "ls": lambda: self.get_long_short_ratio_history(symbol),
            "top_ls": lambda: self.get_top_long_short_ratio_history(symbol) if symbol.upper() in ("BTC", "ETH") else None,
            "options": lambda: self.get_options_info(symbol),
            "taker": lambda: self.get_taker_volume_history(symbol),
            "max_pain": lambda: self.get_option_max_pain(symbol),
            "cvd": lambda: self.get_cvd_history(symbol),
            "net_pos": lambda: self.get_net_position_history(symbol),
            "acc_funding": lambda: self.get_accumulated_funding_rate(symbol),
            "agg_taker": lambda: self.get_aggregated_taker_volume(symbol),
            "orderbook": lambda: self.get_orderbook_imbalance(symbol),
            "eth_btc": lambda: self.get_eth_btc_ratio(),
            "balances": lambda: self.get_exchange_balances(),
            "fg": lambda: self.get_fear_greed_index(),
            "premium": lambda: self.get_coinbase_premium(current_price),
            "stable": lambda: self.get_stablecoin_market_cap_change(),
        }

        results = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_key = {executor.submit(task): key for key, task in tasks.items()}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    logger.error(f"并行获取 {key} 失败: {e}")
                    results[key] = None

        # 组装 data（与之前完全一致，此处省略以节省篇幅）
        # ... 完整组装代码请参考上一轮回复中的 get_all_data 剩余部分 ...
        # 为简洁起见，此处假设您已有完整组装代码，只需替换并发控制部分即可。
        return self._assemble_data(results, symbol, current_price, atr)

    def _assemble_data(self, results: dict, symbol: str, current_price: float, atr: float) -> dict:
        # 此方法内容与之前 get_all_data 中的组装部分完全相同
        # 因篇幅限制，此处不再重复，请从上一轮回复中复制完整组装逻辑
        pass
