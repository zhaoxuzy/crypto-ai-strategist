import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore, Lock
from utils.logger import logger

class RateLimiter:
    def __init__(self, max_requests_per_minute: int = 20):
        self.min_interval = 60.0 / max_requests_per_minute
        self._last_request_time = 0.0
        self._lock = Lock()

    def wait(self):
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_request_time = time.time()


class CoinGlassClient:
    def __init__(self):
        self.api_key = os.getenv("COINGLASS_API_KEY", "")
        if not self.api_key:
            logger.warning("⚠️ 环境变量 COINGLASS_API_KEY 未设置")
        self.base_url = "https://proxy.keystore.com.cn/api/v1/proxy/coinglass/v4"
        self.exchange = "OKX"
        self._rate_limiter = RateLimiter(max_requests_per_minute=20)
        self._max_workers = 3
        self._semaphore = Semaphore(self._max_workers)

    def _request(self, endpoint: str, params: dict = None, max_retries: int = 3, silent_fail: bool = False) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {"accept": "application/json", "X-Api-Key": self.api_key}
        current_params = params.copy() if params else {}

        for attempt in range(max_retries):
            with self._semaphore:
                self._rate_limiter.wait()
                try:
                    logger.info(f"请求 CoinGlass: {endpoint} | params={current_params}")
                    resp = requests.get(url, params=current_params, headers=headers, timeout=15)
                    data = resp.json()
                    if data.get("code") in (0, "0"):
                        return data.get("data", {})
                    else:
                        msg = f"CoinGlass API 错误: {data.get('msg', data)}"
                        if "rate limit" in str(msg).lower():
                            time.sleep(10)
                            continue
                        if attempt < max_retries - 1:
                            time.sleep(2 ** (attempt + 1))
                            continue
                        if silent_fail:
                            logger.warning(f"CoinGlass 数据获取失败（静默）: {msg}")
                            return {}
                        raise RuntimeError(msg)
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(2 ** (attempt + 1))
                        continue
                    if silent_fail:
                        logger.warning(f"CoinGlass 请求异常（静默）: {e}")
                        return {}
                    raise RuntimeError(f"CoinGlass 请求失败: {e}")
        return {}

    # ---------- 辅助函数 ----------
    @staticmethod
    def _get_close_from_candle(candle) -> float:
        if isinstance(candle, list) and len(candle) >= 5:
            return float(candle[4])
        elif isinstance(candle, dict):
            return float(candle.get("close", 0))
        return 0.0

    @staticmethod
    def _calc_percentile(history: list, current: float) -> float:
        if not history:
            return 50.0
        values = [CoinGlassClient._get_close_from_candle(item) for item in history]
        values.sort()
        rank = sum(1 for v in values if v < current)
        return round((rank / len(values)) * 100, 2)

    @staticmethod
    def _calc_slope(series: list) -> float:
        if len(series) < 2:
            return 0.0
        n = len(series)
        x_mean = (n - 1) / 2
        y_mean = sum(series) / n
        numerator = sum((i - x_mean) * (series[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        return numerator / denominator if denominator != 0 else 0.0

    @staticmethod
    def _calc_atr(closes: list, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 0.0
        trs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
        return sum(trs[-period:]) / period if len(trs) >= period else 0.0

    @staticmethod
    def _calc_atr_list(closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        trs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
        atrs = []
        for i in range(period - 1, len(trs)):
            atrs.append(sum(trs[i-period+1:i+1]) / period)
        return atrs

    # ---------- API 方法 ----------
    def _get_symbol(self, base: str) -> str:
        return f"{base}-USDT-SWAP"

    def get_kline_history(self, symbol: str = "BTC", interval: str = "4h", limit: int = 168):
        params = {"exchange": self.exchange, "symbol": self._get_symbol(symbol), "interval": interval, "limit": limit}
        return self._request("api/futures/price/history", params, silent_fail=True)

    def get_oi_ohlc_history(self, symbol: str = "BTC", interval: str = "4h", limit: int = 168):
        params = {"exchange": self.exchange, "symbol": self._get_symbol(symbol), "interval": interval, "limit": limit}
        return self._request("api/futures/open-interest/history", params, silent_fail=True)

    def get_weighted_funding_rate_history(self, symbol: str = "BTC", interval: str = "4h", limit: int = 168):
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        return self._request("api/futures/funding-rate/oi-weight-history", params, silent_fail=True)

    def get_liquidation_heatmap(self, symbol: str = "BTC"):
        params = {"exchange": self.exchange, "symbol": self._get_symbol(symbol), "range": "3d"}
        return self._request("api/futures/liquidation/heatmap/model2", params, silent_fail=True)

    def get_top_long_short_ratio_history(self, symbol: str = "BTC", interval: str = "4h", limit: int = 168):
        params = {"exchange": self.exchange, "symbol": self._get_symbol(symbol), "interval": interval, "limit": limit}
        return self._request("api/futures/top-long-short-position-ratio/history", params, silent_fail=True)

    def get_cvd_history(self, symbol: str = "BTC", interval: str = "1m", limit: int = 240):
        params = {"exchange": self.exchange, "symbol": self._get_symbol(symbol), "interval": interval, "limit": limit}
        return self._request("api/futures/cvd/history", params, silent_fail=True)

    def get_option_max_pain(self, symbol: str = "BTC") -> float:
        params = {"exchange": "Deribit", "symbol": symbol}
        data = self._request("api/option/max-pain", params, silent_fail=True)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("exchange") == "Deribit":
                    return float(item.get("maxPainPrice", 0.0))
        elif isinstance(data, dict):
            return float(data.get("maxPainPrice", 0.0))
        return 0.0

    def get_fear_and_greed_index(self) -> dict:
        data = self._request("api/index/fear-greed-history", {}, silent_fail=True)
        if data and isinstance(data, list) and len(data) >= 8:
            return {
                "current": data[0].get("value", 50),
                "prev_7d": data[7].get("value", 50)
            }
        return {"current": 50, "prev_7d": 50}

    # ---------- 新增指标 ----------
    def get_netflow(self, symbol: str = "BTC") -> float:
        """期货资金净流入/流出（24h），单位 USDT"""
        params = {"symbol": symbol}
        data = self._request("api/futures/coin/netflow", params, silent_fail=True)
        if isinstance(data, dict):
            return float(data.get("netflow", 0.0))
        return 0.0

    def get_orderbook_imbalance(self, symbol: str = "BTC") -> dict:
        """订单簿买卖深度及失衡率"""
        params = {"exchange": self.exchange, "symbol": self._get_symbol(symbol), "interval": "1m", "limit": 1}
        data = self._request("api/futures/orderbook/ask-bids-history", params, silent_fail=True)
        if data and isinstance(data, list) and len(data) > 0:
            latest = data[0]
            bids = float(latest.get("bids_usd", 0))
            asks = float(latest.get("asks_usd", 0))
            total = bids + asks
            imbalance = (bids - asks) / total if total > 0 else 0.0
            return {"bids_usd": bids, "asks_usd": asks, "imbalance": round(imbalance, 4)}
        return {"bids_usd": 0.0, "asks_usd": 0.0, "imbalance": 0.0}

    def get_exchange_btc_balance(self) -> dict:
        """全市场交易所 BTC 余额及 24h 变化"""
        data = self._request("api/exchange/balance/list", {"symbol": "BTC"}, silent_fail=True)
        if data and isinstance(data, list):
            total = sum(float(ex.get("balance", 0)) for ex in data)
            change_24h = sum(float(ex.get("balance_change_1d", 0)) for ex in data)
            return {"total_btc": total, "change_24h": change_24h}
        return {"total_btc": 0.0, "change_24h": 0.0}

    def get_aggregated_oi_history(self, symbol: str = "BTC", interval: str = "4h", limit: int = 168):
        """全市场持仓量聚合历史"""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        return self._request("api/futures/open-interest/aggregated-history", params, silent_fail=True)

    def get_eth_btc_ratio(self) -> float:
        try:
            eth = self.get_kline_history("ETH", "4h", 1)
            btc = self.get_kline_history("BTC", "4h", 1)
            eth_close = self._get_close_from_candle(eth[0]) if eth else 0
            btc_close = self._get_close_from_candle(btc[0]) if btc else 1
            return eth_close / btc_close if btc_close > 0 else 0.0
        except:
            return 0.0

    # ---------- 聚合数据 ----------
    def get_all_data(self, symbol: str = "BTC") -> dict:
        base_symbol = symbol.upper()

        tasks = {
            "kline": lambda: self.get_kline_history(base_symbol, "4h", 168),
            "oi": lambda: self.get_oi_ohlc_history(base_symbol, "4h", 168),
            "funding": lambda: self.get_weighted_funding_rate_history(base_symbol, "4h", 168),
            "heatmap": lambda: self.get_liquidation_heatmap(base_symbol),
            "top_ls": lambda: self.get_top_long_short_ratio_history(base_symbol, "4h", 168),
            "cvd": lambda: self.get_cvd_history(base_symbol, "1m", 240),
            "max_pain": lambda: self.get_option_max_pain(base_symbol),
            "fg": lambda: self.get_fear_and_greed_index(),
            "netflow": lambda: self.get_netflow(base_symbol),
            "orderbook": lambda: self.get_orderbook_imbalance(base_symbol),
            "exchange_btc": lambda: self.get_exchange_btc_balance(),
            "agg_oi": lambda: self.get_aggregated_oi_history(base_symbol, "4h", 168),
        }

        results = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(task): key for key, task in tasks.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    logger.error(f"获取 {key} 失败: {e}")
                    results[key] = None

        eth_btc_ratio = self.get_eth_btc_ratio()

        # 数据质量标记
        data_quality = {}
        for key in tasks.keys():
            if key == "fg":
                data_quality["恐慌贪婪指数"] = "✅" if results.get(key) else "⚠️ 回退"
            elif key == "exchange_btc":
                data_quality["交易所BTC余额"] = "✅" if results.get(key) else "⚠️ 回退"
            else:
                data_quality[key] = "✅" if results.get(key) else "❌ 缺失"

        # 解析数据
        kline_data = results.get("kline", [])
        oi_data = results.get("oi", [])
        funding_data = results.get("funding", [])
        top_ls_data = results.get("top_ls", [])
        cvd_data = results.get("cvd", [])
        heatmap_raw = results.get("heatmap", {})
        max_pain = results.get("max_pain", 0.0)
        fg_data = results.get("fg", {"current": 50, "prev_7d": 50})
        netflow = results.get("netflow", 0.0)
        orderbook = results.get("orderbook", {"bids_usd": 0.0, "asks_usd": 0.0, "imbalance": 0.0})
        exchange_btc = results.get("exchange_btc", {"total_btc": 0.0, "change_24h": 0.0})
        agg_oi_data = results.get("agg_oi", [])

        mark_price = self._get_close_from_candle(kline_data[-1]) if kline_data else 0.0
        closes = [self._get_close_from_candle(k) for k in kline_data]
        atr = self._calc_atr(closes, 14) if len(closes) >= 14 else 0.0
        avg_atr_7d = sum(self._calc_atr_list(closes, 14)) / len(closes) if closes else 1.0
        vol_factor = atr / avg_atr_7d if avg_atr_7d > 0 else 1.0
        price_percentile = self._calc_percentile(kline_data, mark_price)

        above_liq, below_liq, above_cluster, below_cluster, liq_ratio = 0, 0, "N/A", "N/A", 0.0
        if heatmap_raw:
            y_axis = heatmap_raw.get("y_axis", [])
            liq_data = heatmap_raw.get("liquidation_leverage_data", [])
            pain_map = {}
            for item in liq_data:
                if isinstance(item, list) and len(item) >= 3:
                    price = float(y_axis[int(item[1])]) if int(item[1]) < len(y_axis) else 0
                    intensity = float(item[2])
                    if price > mark_price: above_liq += intensity
                    elif price < mark_price: below_liq += intensity
                    pain_map[price] = intensity
            liq_ratio = above_liq / below_liq if below_liq > 0 else 0.0
            if pain_map:
                above_prices = [p for p in pain_map if p > mark_price]
                below_prices = [p for p in pain_map if p < mark_price]
                if above_prices:
                    max_above = max(above_prices, key=lambda p: pain_map[p])
                    above_cluster = f"{max_above*0.99:.0f}-{max_above*1.01:.0f}"
                if below_prices:
                    max_below = max(below_prices, key=lambda p: pain_map[p])
                    below_cluster = f"{max_below*0.99:.0f}-{max_below*1.01:.0f}"

        oi_current = self._get_close_from_candle(oi_data[-1]) if oi_data else 0.0
        oi_percentile = self._calc_percentile(oi_data, oi_current)
        oi_change_24h = 0.0
        if len(oi_data) >= 6:
            oi_24h_ago = self._get_close_from_candle(oi_data[-6])
            oi_change_24h = (oi_current - oi_24h_ago) / oi_24h_ago * 100 if oi_24h_ago > 0 else 0.0

        funding_current = self._get_close_from_candle(funding_data[-1]) if funding_data else 0.0
        funding_percentile = self._calc_percentile(funding_data, funding_current)

        top_ls_current = self._get_close_from_candle(top_ls_data[-1]) if top_ls_data else 0.0
        top_ls_percentile = self._calc_percentile(top_ls_data, top_ls_current)

        cvd_series = [self._get_close_from_candle(c) for c in cvd_data] if cvd_data else []
        cvd_mean = sum(cvd_series) / len(cvd_series) / 1e6 if cvd_series else 0.0
        cvd_slope = self._calc_slope(cvd_series)

        agg_oi_current = self._get_close_from_candle(agg_oi_data[-1]) if agg_oi_data else 0.0
        agg_oi_change_24h = 0.0
        if len(agg_oi_data) >= 6:
            agg_oi_24h_ago = self._get_close_from_candle(agg_oi_data[-6])
            agg_oi_change_24h = (agg_oi_current - agg_oi_24h_ago) / agg_oi_24h_ago * 100 if agg_oi_24h_ago > 0 else 0.0

        fear_greed = fg_data.get("current", 50)
        fear_greed_prev_7d = fg_data.get("prev_7d", 50)

        return {
            "mark_price": mark_price,
            "atr": atr,
            "vol_factor": vol_factor,
            "price_percentile": price_percentile,
            "above_liq": above_liq,
            "below_liq": below_liq,
            "liq_ratio": liq_ratio,
            "above_cluster": above_cluster,
            "below_cluster": below_cluster,
            "max_pain": max_pain,
            "top_ls_ratio": top_ls_current,
            "top_ls_percentile": top_ls_percentile,
            "funding_rate": funding_current,
            "funding_percentile": funding_percentile,
            "oi": oi_current,
            "oi_percentile": oi_percentile,
            "oi_change_24h": oi_change_24h,
            "agg_oi": agg_oi_current,
            "agg_oi_change_24h": agg_oi_change_24h,
            "cvd_mean": cvd_mean,
            "cvd_slope": cvd_slope,
            "fear_greed": fear_greed,
            "fear_greed_prev_7d": fear_greed_prev_7d,
            "eth_btc_ratio": eth_btc_ratio,
            "netflow": netflow,
            "orderbook_bids": orderbook.get("bids_usd", 0.0),
            "orderbook_asks": orderbook.get("asks_usd", 0.0),
            "orderbook_imbalance": orderbook.get("imbalance", 0.0),
            "exchange_btc_total": exchange_btc.get("total_btc", 0.0),
            "exchange_btc_change_24h": exchange_btc.get("change_24h", 0.0),
            "data_quality": data_quality
        }
