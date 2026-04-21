import os
import time
import requests
from utils.logger import logger

class CoinGlassClient:
    def __init__(self):
        self.api_key = os.getenv("COINGLASS_API_KEY", "")
        if not self.api_key:
            logger.warning("⚠️ 环境变量 COINGLASS_API_KEY 未设置，API 请求将失败")
        self.base_url = "https://proxy.keystore.com.cn/api/v1/proxy/coinglass/v4"
        self.primary_exchange = "OKX"
        # 严格控制请求间隔，20 req/min = 每 3 秒一次
        self._min_interval = 3.0
        self._last_request_time = 0.0

    def _wait_for_rate_limit(self):
        """强制等待以保证请求间隔 ≥ 3 秒"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            sleep_time = self._min_interval - elapsed
            logger.debug(f"速率限制等待 {sleep_time:.2f} 秒")
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _request(self, endpoint: str, params: dict = None, max_retries: int = 3, silent_fail: bool = False) -> dict:
        """发送单次请求，自动处理速率限制和重试"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {"accept": "application/json", "X-Api-Key": self.api_key}
        current_params = params.copy() if params else {}

        for attempt in range(max_retries):
            self._wait_for_rate_limit()
            try:
                logger.info(f"请求 CoinGlass: {endpoint} | params={current_params}")
                resp = requests.get(url, params=current_params, headers=headers, timeout=15)
                data = resp.json()
                if data.get("code") in (0, "0"):
                    return data.get("data", {})
                else:
                    msg = f"CoinGlass API 错误: {data.get('msg', data)}"
                    # 如果是速率限制错误，额外等待
                    if "rate limit" in str(msg).lower():
                        logger.warning(f"速率限制触发，等待 10 秒...")
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

    @staticmethod
    def _get_close_from_candle(candle) -> float:
        if isinstance(candle, list) and len(candle) >= 5: return float(candle[4])
        elif isinstance(candle, dict): return float(candle.get("close", 0))
        return 0.0

    @staticmethod
    def _calc_percentile(history: list, current: float) -> float:
        if not history: return 50.0
        values = [CoinGlassClient._get_close_from_candle(item) for item in history]
        values.sort()
        rank = sum(1 for v in values if v < current)
        return round((rank / len(values)) * 100, 2)

    @staticmethod
    def _calc_slope(series: list) -> float:
        if len(series) < 2: return 0.0
        n = len(series)
        x_mean = (n - 1) / 2
        y_mean = sum(series) / n
        numerator = sum((i - x_mean) * (series[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        return numerator / denominator if denominator != 0 else 0.0

    # ---------- API 方法（全部串行调用，无内部重试交易所切换）----------
    def get_kline_history(self, symbol: str = "BTCUSDT", interval: str = "4h", limit: int = 168):
        params = {"exchange": self.primary_exchange, "symbol": symbol, "interval": interval, "limit": limit}
        return self._request("api/futures/price/history", params, silent_fail=True)

    def get_oi_ohlc_history(self, symbol: str = "BTCUSDT", interval: str = "4h", limit: int = 168):
        params = {"exchange": self.primary_exchange, "symbol": symbol, "interval": interval, "limit": limit}
        return self._request("api/futures/open-interest/history", params, silent_fail=True)

    def get_weighted_funding_rate_history(self, symbol: str = "BTC", interval: str = "4h", limit: int = 168):
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        return self._request("api/futures/funding-rate/oi-weight-history", params, silent_fail=True)

    def get_liquidation_heatmap(self, symbol: str = "BTCUSDT"):
        params = {"exchange": self.primary_exchange, "symbol": symbol, "range": "3d"}
        return self._request("api/futures/liquidation/heatmap/model2", params, silent_fail=True)

    def get_top_long_short_ratio_history(self, symbol: str = "BTCUSDT", interval: str = "4h", limit: int = 168):
        params = {"exchange": self.primary_exchange, "symbol": symbol, "interval": interval, "limit": limit}
        return self._request("api/futures/top-long-short-position-ratio/history", params, silent_fail=True)

    def get_cvd_history(self, symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 240):
        params = {"exchange": self.primary_exchange, "symbol": symbol, "interval": interval, "limit": limit}
        return self._request("api/futures/cvd/history", params, silent_fail=True)

    def get_option_max_pain(self, symbol: str = "BTC"):
        params = {"symbol": symbol}
        return self._request("api/option/max-pain", params, silent_fail=True)

    def get_fear_and_greed_index(self) -> dict:
        data = self._request("api/index/fear-greed-history", {}, silent_fail=True)
        if data and isinstance(data, list) and len(data) >= 2:
            return {"current": data[0].get("value", 50), "prev": data[1].get("value", 50)}
        return {"current": 50, "prev": 50}

    def get_eth_btc_ratio(self) -> float:
        try:
            eth = self.get_kline_history("ETHUSDT", "4h", 1)
            btc = self.get_kline_history("BTCUSDT", "4h", 1)
            eth_close = self._get_close_from_candle(eth[0]) if eth else 0
            btc_close = self._get_close_from_candle(btc[0]) if btc else 1
            return eth_close / btc_close if btc_close > 0 else 0.0
        except:
            return 0.0

    def get_all_data(self, symbol: str = "BTC") -> dict:
        base_symbol = symbol.upper()
        usdt_symbol = f"{base_symbol}USDT"

        # 串行获取所有数据（每个请求间隔 ≥ 3 秒）
        kline_data = self.get_kline_history(usdt_symbol, "4h", 168)
        oi_data = self.get_oi_ohlc_history(usdt_symbol, "4h", 168)
        funding_data = self.get_weighted_funding_rate_history(base_symbol, "4h", 168)
        heatmap_raw = self.get_liquidation_heatmap(usdt_symbol)
        top_ls_data = self.get_top_long_short_ratio_history(usdt_symbol, "4h", 168)
        cvd_data = self.get_cvd_history(usdt_symbol, "1m", 240)
        max_pain_data = self.get_option_max_pain(base_symbol)
        fg_data = self.get_fear_and_greed_index()
        eth_btc_ratio = self.get_eth_btc_ratio()

        # 计算衍生指标
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

        max_pain = max_pain_data.get("maxPainPrice", 0.0) if max_pain_data else 0.0
        fear_greed = fg_data.get("current", 50)

        return {
            "mark_price": mark_price, "atr": atr, "vol_factor": vol_factor, "price_percentile": price_percentile,
            "above_liq": above_liq, "below_liq": below_liq, "liq_ratio": liq_ratio,
            "above_cluster": above_cluster, "below_cluster": below_cluster, "max_pain": max_pain,
            "top_ls_ratio": top_ls_current, "top_ls_percentile": top_ls_percentile,
            "funding_rate": funding_current, "funding_percentile": funding_percentile,
            "oi": oi_current, "oi_percentile": oi_percentile, "oi_change_24h": oi_change_24h,
            "cvd_mean": cvd_mean, "cvd_slope": cvd_slope,
            "fear_greed": fear_greed, "eth_btc_ratio": eth_btc_ratio,
        }

    def _calc_atr(self, closes: list, period: int = 14) -> float:
        if len(closes) < period + 1: return 0.0
        trs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
        return sum(trs[-period:]) / period if len(trs) >= period else 0.0

    def _calc_atr_list(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1: return []
        trs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
        atrs = []
        for i in range(period - 1, len(trs)):
            atrs.append(sum(trs[i-period+1:i+1]) / period)
        return atrs
