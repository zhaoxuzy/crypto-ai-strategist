import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from collections import deque
from utils.logger import logger
from data_fetcher.coinglass import CoinGlassClient
from data_fetcher.okx_rest import get_current_price, calculate_atr, get_klines, calculate_ema, calculate_atr_percentile, calculate_ema_slope
from ai_client.deepseek import call_deepseek_enhanced, validate_strategy_enhanced, calculate_signal_strength
from notifier.dingtalk import send_dingtalk_message, format_strategy_message

SYMBOL_MAP = {"BTC": "BTC-USDT-SWAP", "ETH": "ETH-USDT-SWAP", "SOL": "SOL-USDT-SWAP"}
STRATEGY_PROFILES = {
    "BTC": {"stop_multiplier": 2.0, "tp_ratio": 2.0, "volatility_discount": 0.8},
    "ETH": {"stop_multiplier": 2.0, "tp_ratio": 2.0, "volatility_discount": 0.7},
    "SOL": {"stop_multiplier": 2.5, "tp_ratio": 2.0, "volatility_discount": 0.6}
}

probe_history = deque(maxlen=20)
EXTREME_LIQ_THRESHOLDS = {"BTC": 200_000_000, "ETH": 80_000_000, "SOL": 20_000_000}


def calculate_trend_strength(klines: list, cvd_signal: str, taker_ratio: float, current_price: float, current_atr: float, liq_dynamic_signals: list = None) -> dict:
    if not klines or len(klines) < 55:
        return {"direction": "neutral", "score": 0, "confidence": "低", "signals": [], "transition": False}
    ema55 = calculate_ema(klines, 55)
    ema_slope = calculate_ema_slope(klines, 55, 5)
    atr_percentile = calculate_atr_percentile(klines, current_atr, 20)
    score, signals, direction = 0, [], "neutral"
    if current_price < ema55:
        score += 35; signals.append("价格<EMA55"); direction = "bear"
    else:
        signals.append("价格>EMA55"); direction = "bull"
    if ema_slope < -2.0:
        score += 25; signals.append("EMA斜率向下")
    elif ema_slope > 2.0:
        score += 25; signals.append("EMA斜率向上")
    else:
        score += 10; signals.append("EMA斜率走平")
    if cvd_signal in ["bearish", "slightly_bearish"]:
        score += 25 if cvd_signal == "bearish" else 15
        signals.append(f"CVD:{cvd_signal}")
        if direction == "neutral": direction = "bear"
    elif cvd_signal in ["bullish", "slightly_bullish"]:
        score += 25 if cvd_signal == "bullish" else 15
        signals.append(f"CVD:{cvd_signal}")
        if direction == "neutral": direction = "bull"
    else:
        score += 5; signals.append("CVD:neutral")
    if taker_ratio < 0.45:
        score += 15; signals.append(f"主动卖盘({taker_ratio:.2f})")
        if direction == "neutral": direction = "bear"
    elif taker_ratio > 0.55:
        score += 15; signals.append(f"主动买盘({taker_ratio:.2f})")
        if direction == "neutral": direction = "bull"
    else:
        score += 5; signals.append("主动买卖均衡")
    if atr_percentile < 30:
        score = int(score * 0.7); signals.append(f"低波动(ATR百分位{atr_percentile:.0f}%)")

    if liq_dynamic_signals:
        for sig in liq_dynamic_signals:
            if "清算压力偏空" in sig:
                score += 10; signals.append(sig)
                if direction == "neutral": direction = "bear"
            elif "清算压力偏多" in sig:
                score += 10; signals.append(sig)
                if direction == "neutral": direction = "bull"
            elif "最大痛点上移" in sig:
                score += 8; signals.append(sig)
                if direction == "neutral": direction = "bull"
            elif "最大痛点下移" in sig:
                score += 8; signals.append(sig)
                if direction == "neutral": direction = "bear"
            elif "强磁吸区" in sig:
                score += 6; signals.append(sig)
            elif "清算堆积加速" in sig:
                score += 5; signals.append(sig)
            elif "清算堆积衰减" in sig:
                score -= 3; signals.append(sig)

    score = max(0, min(100, score))
    confidence = "高" if score >= 60 else ("中" if score >= 35 else "低")
    transition = 30 <= score <= 70
    return {"direction": direction, "score": score, "confidence": confidence, "signals": signals, "ema55": ema55, "ema_slope": ema_slope, "atr_percentile": atr_percentile, "transition": transition}


def get_key_levels(coinglass_data: dict, ema55: float) -> dict:
    cluster = coinglass_data.get("nearest_cluster", {})
    cluster_price = float(cluster.get("price", 0)) if cluster.get("price", "N/A") != "N/A" else 0
    cluster_intensity = int(cluster.get("intensity", 0)) if cluster.get("intensity", "N/A") != "N/A" else 0
    max_pain = float(coinglass_data.get("max_pain_price", 0)) if coinglass_data.get("max_pain_price", "N/A") != "N/A" else 0
    option_pain = float(coinglass_data.get("skew", 0)) if coinglass_data.get("skew", "N/A") != "N/A" else 0
    support = ema55
    resistance = ema55
    if cluster_intensity >= 3 and cluster_price > 0:
        direction = cluster.get("direction", "")
        if direction == "上": resistance = cluster_price
        elif direction == "下": support = cluster_price
    if max_pain > 0:
        if max_pain < ema55 and max_pain > support: support = max_pain
        elif max_pain > ema55 and max_pain < resistance: resistance = max_pain
    if option_pain > 0:
        if option_pain < ema55 and option_pain > support: support = option_pain
        elif option_pain > ema55 and option_pain < resistance: resistance = option_pain
    return {"support": support, "resistance": resistance}


def compute_macro_three_factor_score(cg: CoinGlassClient, cg_data: dict, btc_price: float) -> dict:
    bull_score = 0
    bear_score = 0
    signals = []
    
    fg = cg_data.get("fear_greed_index", {"current": 50, "prev": 50})
    fg_current = fg.get("current", 50)
    fg_prev = fg.get("prev", fg_current)
    
    if fg_current <= 30:
        if fg_current > fg_prev:
            bull_score += 4
            signals.append({"text": f"极恐反弹({fg_current}↑{fg_prev})", "direction": "利多", "weight": 4})
        else:
            bull_score += 2
            signals.append({"text": f"极恐钝化({fg_current}≤{fg_prev})", "direction": "偏多", "weight": 2})
    elif fg_current >= 70:
        if fg_current > fg_prev:
            bear_score += 3
            signals.append({"text": f"贪婪加速({fg_current}↑{fg_prev})", "direction": "利空", "weight": 3})
        else:
            bear_score += 1
            signals.append({"text": f"贪婪筑顶({fg_current}≤{fg_prev})", "direction": "偏空", "weight": 1})
    
    premium_data = cg_data.get("coinbase_premium", {"premium_pct": 0.0})
    premium_pct = premium_data.get("premium_pct", 0.0)
    if premium_pct > 0.15:
        bull_score += 3
        signals.append({"text": f"Coinbase溢价({premium_pct:.2f}%)", "direction": "利多", "weight": 3})
    elif premium_pct < -0.15:
        bear_score += 3
        signals.append({"text": f"Coinbase折价({premium_pct:.2f}%)", "direction": "利空", "weight": 3})
    
    stable_data = cg_data.get("stablecoin_change", {"change_7d": 0.0})
    change_7d = stable_data.get("change_7d", 0.0)
    if change_7d > 1.0:
        bull_score += 3
        signals.append({"text": f"稳定币增发({change_7d:.2f}%)", "direction": "利多", "weight": 3})
    elif change_7d < -1.0:
        bear_score += 3
        signals.append({"text": f"稳定币赎回({-change_7d:.2f}%)", "direction": "利空", "weight": 3})
    
    total = bull_score + bear_score
    return {
        "bull_score": bull_score,
        "bear_score": bear_score,
        "total": total,
        "signals": signals
    }


def compute_directional_scores_v2(symbol: str, cg_data: dict, trend_info: dict, cg: CoinGlassClient, btc_price: float, atr: float) -> dict:
    bull_score = 0
    bear_score = 0
    current_price = cg_data.get("current_price", btc_price)
    trend_score = trend_info.get("score", 0)
    trend_dir = trend_info.get("direction", "neutral")
    
    # 1. 清算不对称比率（14分）
    above = float(str(cg_data.get("above_short_liquidation", "0")).replace(",", ""))
    below = float(str(cg_data.get("below_long_liquidation", "0")).replace(",", ""))
    total_liq = above + below
    if total_liq > 0:
        ratio = above / total_liq
        if ratio > 0.65:
            bear_score += 14
        elif ratio > 0.55:
            bear_score += 8
        elif ratio < 0.35:
            bull_score += 14
        elif ratio < 0.45:
            bull_score += 8
    
    # 2. 趋势强度（33分，分段映射）
    if trend_score >= 75:
        trend_component = 33
    elif trend_score >= 60:
        trend_component = 25
    elif trend_score >= 45:
        trend_component = 16
    elif trend_score >= 30:
        trend_component = 8
    else:
        trend_component = 0
    if trend_dir == "bull":
        bull_score += trend_component
    elif trend_dir == "bear":
        bear_score += trend_component
    
    # 3. CVD信号（17分）
    cvd = cg_data.get("cvd_signal", "N/A")
    if cvd == "bullish":
        bull_score += 17
    elif cvd == "slightly_bullish":
        bull_score += 9
    elif cvd == "bearish":
        bear_score += 17
    elif cvd == "slightly_bearish":
        bear_score += 9
    
    # 4. 主动买卖盘比率（7分）
    try:
        tr = float(cg_data.get("taker_ratio", 0.5))
        if tr >= 0.55:
            bull_score += 7
        elif tr >= 0.50:
            bull_score += 3
        elif tr <= 0.45:
            bear_score += 7
        elif tr <= 0.50:
            bear_score += 3
    except:
        pass
    
    # 5. 顶级交易员多空比（5分）
    try:
        tls = float(cg_data.get("top_long_short_ratio", 1.0))
        if tls < 0.7:
            bull_score += 5
        elif tls < 1.0:
            bull_score += 2
        elif tls > 2.0:
            bear_score += 5
        elif tls > 1.0:
            bear_score += 2
    except:
        pass
    
    # 6. 宏观三因子（5分）
    macro_result = compute_macro_three_factor_score(cg, cg_data, btc_price)
    macro_total = macro_result["total"]
    if macro_total >= 7:
        macro_component = 5
    elif macro_total >= 4:
        macro_component = 2
    else:
        macro_component = 0
    if macro_result["bull_score"] > macro_result["bear_score"]:
        bull_score += macro_component
    elif macro_result["bear_score"] > macro_result["bull_score"]:
        bear_score += macro_component
    
    # 7. ETH/BTC汇率趋势（5分）
    eth_btc = cg_data.get("eth_btc_ratio", {})
    if eth_btc:
        trend = eth_btc.get("trend", "neutral")
        if trend == "up":
            bull_score += 5
        elif trend == "down":
            bear_score += 5
    
    # 8. 清算动态信号（14分）
    liq_dynamic = cg_data.get("liq_dynamic_signals", [])
    dynamic_bull = 0
    dynamic_bear = 0
    for sig in liq_dynamic:
        if "清算压力偏多" in sig or "最大痛点上移" in sig:
            dynamic_bull = max(dynamic_bull, 14)
        elif "清算压力偏空" in sig or "最大痛点下移" in sig:
            dynamic_bear = max(dynamic_bear, 14)
        elif "强磁吸区" in sig:
            if "上" in sig:
                dynamic_bear = max(dynamic_bear, 7)
            else:
                dynamic_bull = max(dynamic_bull, 7)
        elif "清算堆积加速" in sig:
            if "偏多" in sig:
                dynamic_bull += 4
            else:
                dynamic_bear += 4
        elif "清算堆积衰减" in sig:
            if "偏多" in sig:
                dynamic_bull -= 4
            else:
                dynamic_bear -= 4
    bull_score += min(14, max(0, dynamic_bull))
    bear_score += min(14, max(0, dynamic_bear))
    
    # 9. 价格位置惩罚（-10分）
    cluster = cg_data.get("nearest_cluster", {})
    cluster_price = float(cluster.get("price", 0)) if cluster.get("price", "N/A") != "N/A" else 0
    cluster_dir = cluster.get("direction", "")
    cluster_intensity = int(cluster.get("intensity", 0)) if cluster.get("intensity", "N/A") != "N/A" else 0
    
    if cluster_intensity >= 3 and cluster_price > 0 and atr > 0:
        distance_atr = abs(current_price - cluster_price) / atr
        if distance_atr < 0.3:
            if cluster_dir == "上" and trend_score < 70:
                bull_score = max(0, bull_score - 10)
            elif cluster_dir == "下" and trend_score < 70:
                bear_score = max(0, bear_score - 10)
    
    bull_score = max(0, bull_score)
    bear_score = max(0, bear_score)
    
    return {
        "bull": bull_score,
        "bear": bear_score,
        "macro_signals": macro_result["signals"]
    }


def get_entry_candidates(price: float, atr: float, direction: str, cluster: dict, ema55: float, key_levels: dict) -> dict:
    candidates = {
        "rule1": {"low": 0.0, "high": 0.0, "anchor": ""},
        "rule2": {"low": 0.0, "high": 0.0, "anchor": ""},
        "rule3": {"low": 0.0, "high": 0.0, "anchor": ""}
    }
    min_width = 0.5 * atr

    cluster_price = float(cluster.get("price", 0)) if cluster.get("price", "N/A") != "N/A" else 0
    cluster_intensity = int(cluster.get("intensity", 0)) if cluster.get("intensity", "N/A") != "N/A" else 0
    cluster_dir = cluster.get("direction", "")
    if cluster_intensity >= 3 and cluster_price > 0:
        if (direction == "long" and cluster_dir == "下") or (direction == "short" and cluster_dir == "上"):
            width = max(min_width, cluster_price * 0.002)
            candidates["rule1"] = {
                "low": round(cluster_price - width/2, 1),
                "high": round(cluster_price + width/2, 1),
                "anchor": f"同向清算区 {cluster_price:.1f}"
            }

    if direction == "long":
        key_price = key_levels.get("support", ema55)
    else:
        key_price = key_levels.get("resistance", ema55)
    width = max(min_width, key_price * 0.004)
    candidates["rule2"] = {
        "low": round(key_price - width/2, 1),
        "high": round(key_price + width/2, 1),
        "anchor": f"{'支撑' if direction == 'long' else '阻力'}位 {key_price:.1f}"
    }

    width = 2.0 * atr
    center = price
    candidates["rule3"] = {
        "low": round(center - width/2, 1),
        "high": round(center + width/2, 1),
        "anchor": "ATR追单区间 (2×ATR)"
    }

    return candidates


def get_tp_candidates(price: float, atr: float, direction: str, cluster: dict, stop_loss: float, volatility_factor: float = 1.0) -> dict:
    """
    返回三个止盈候选值
    rule1: 最近反方向清算区（强度≥3）
    rule2: 更优清算区（强度≥4 且 盈亏比 ≥ 1.5:1）
    rule3: 2:1 盈亏比公式计算值
    """
    candidates = {
        "rule1": {"price": 0.0, "anchor": ""},
        "rule2": {"price": 0.0, "anchor": ""},
        "rule3": {"price": 0.0, "anchor": "2:1盈亏比公式"}
    }
    
    cluster_price = float(cluster.get("price", 0)) if cluster.get("price", "N/A") != "N/A" else 0
    cluster_intensity = int(cluster.get("intensity", 0)) if cluster.get("intensity", "N/A") != "N/A" else 0
    cluster_dir = cluster.get("direction", "")
    
    # rule3: 2:1 公式
    risk = abs(price - stop_loss)
    if direction == "long":
        candidates["rule3"]["price"] = round(price + 2.0 * risk, 1)
    else:
        candidates["rule3"]["price"] = round(price - 2.0 * risk, 1)
    
    # rule1: 最近反方向清算区
    if cluster_intensity >= 3 and cluster_price > 0:
        if (direction == "long" and cluster_dir == "上" and cluster_price > price) or \
           (direction == "short" and cluster_dir == "下" and cluster_price < price):
            candidates["rule1"]["price"] = round(cluster_price, 1)
            candidates["rule1"]["anchor"] = f"反方向清算区 {cluster_price:.1f} (强度{cluster_intensity})"
            if direction == "long":
                reward = cluster_price - price
            else:
                reward = price - cluster_price
            if reward > 0 and reward / risk < 1.0:
                candidates["rule1"]["anchor"] += " [盈亏比<1:1]"
    
    # rule2: 更优清算区（当前简化，使用 rule3 代替）
    candidates["rule2"]["price"] = candidates["rule3"]["price"]
    candidates["rule2"]["anchor"] = "更优清算区(暂用公式)"
    
    return candidates


def send_error_notification(symbol: str, error_msg: str):
    beijing_tz = timezone(timedelta(hours=8))
    now_str = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M")
    markdown = f"""## ❌ DeepSeek 策略生成失败 [{symbol}] 🕒 {now_str}
### 错误详情
> {error_msg}
### 处理建议
- 请检查数据源（CoinGlass、OKX）是否正常
- 可稍后手动重试或查看 Actions 日志排查
"""
    send_dingtalk_message(markdown, f"DeepSeek策略异常-{symbol}")


def main():
    global probe_history
    symbol = os.getenv("STRATEGY_SYMBOL", "BTC").upper()
    if symbol not in SYMBOL_MAP: symbol = "BTC"
    profile = STRATEGY_PROFILES.get(symbol, STRATEGY_PROFILES["BTC"]).copy()
    okx_inst_id = SYMBOL_MAP[symbol]
    logger.info(f"===== 策略生成流程开始 ({symbol}) =====")
    try:
        price = get_current_price(okx_inst_id)
        if price <= 0: raise Exception("无法获取当前价格")
        klines = get_klines(okx_inst_id, "4H", 70)
        atr = calculate_atr(okx_inst_id, timeframe="4H")
        ema55 = calculate_ema(klines, 55)
        logger.info(f"{symbol} 当前价格: {price:.2f}, ATR(4H): {atr:.2f}, EMA55: {ema55:.1f}")

        cg = CoinGlassClient()
        cg_data = cg.get_all_data(symbol, current_price=price, atr=atr, klines=klines)
        logger.info(f"{symbol} CoinGlass 数据获取完成")
        liq_zero_count = cg.get_liq_zero_count()
        liq_warning = cg.get_liq_zero_warning()
        if liq_warning: logger.warning(liq_warning)
        data_source_status = cg.get_data_source_status()
        volatility_factor = cg_data.get("volatility_factor", 1.0)

        cvd_signal = cg_data.get("cvd_signal", "neutral")
        taker_ratio = float(cg_data.get("taker_ratio", "0.5")) if cg_data.get("taker_ratio", "N/A") != "N/A" else 0.5
        liq_dynamic_signals = cg_data.get("liq_dynamic_signals", [])

        trend_info = calculate_trend_strength(klines, cvd_signal, taker_ratio, price, atr, liq_dynamic_signals)
        key_levels = get_key_levels(cg_data, ema55)
        directional_scores = compute_directional_scores_v2(symbol, cg_data, trend_info, cg, price, atr)

        above_val = float(str(cg_data.get("above_short_liquidation", "0")).replace(",", ""))
        below_val = float(str(cg_data.get("below_long_liquidation", "0")).replace(",", ""))
        extreme_liq = (above_val > EXTREME_LIQ_THRESHOLDS[symbol]) or (below_val > EXTREME_LIQ_THRESHOLDS[symbol])

        exchange_balances = cg_data.get("exchange_balances", {})

        signal_strength = calculate_signal_strength(
            symbol, "long", cg_data, {"fear_greed": {"value": cg_data.get("fear_greed_index", {}).get("current", 50)}}, liq_zero_count,
            cg_data.get("eth_btc_ratio"), exchange_balances, trend_info, extreme_liq
        )
        score = signal_strength["score"]
        if score >= 65: signal_grade = "A"
        elif score >= 40: signal_grade = "B"
        else: signal_grade = "C"

        # 动态调整强制裁决阈值（波动因子）
        base_threshold_bull_bear = 8
        base_threshold_warning = 12
        if volatility_factor > 1.3:
            threshold_bull_bear = base_threshold_bull_bear + 2
            threshold_warning = base_threshold_warning + 2
        elif volatility_factor < 0.7:
            threshold_bull_bear = max(5, base_threshold_bull_bear - 2)
            threshold_warning = max(8, base_threshold_warning - 2)
        else:
            threshold_bull_bear = base_threshold_bull_bear
            threshold_warning = base_threshold_warning

        temp_direction = trend_info.get("direction", "bull")
        if temp_direction not in ["long", "short"]:
            temp_direction = "long" if temp_direction == "bull" else "short"
        entry_candidates = get_entry_candidates(price, atr, temp_direction, cg_data.get("nearest_cluster", {}), ema55, key_levels)

        # ========== 使用增强版 DeepSeek 调用 ==========
        strategy = call_deepseek_enhanced(
            symbol=symbol,
            price=price,
            atr=atr,
            coinglass_data=cg_data,
            macro_data={"fear_greed": {"value": cg_data.get("fear_greed_index", {}).get("current", 50)}},
            profile=profile,
            volatility_factor=volatility_factor,
            trend_info=trend_info,
            extreme_liq=extreme_liq,
            liq_warning=liq_warning,
            data_source_status=data_source_status,
            directional_scores=directional_scores,
            signal_grade=signal_grade,
            entry_candidates=entry_candidates,
            exchange_balances=exchange_balances,
            liq_dynamic_signals=liq_dynamic_signals,
            threshold_bull_bear=threshold_bull_bear,
            threshold_warning=threshold_warning,
            tp_candidates=None
        )
        # =============================================

        if not strategy: raise Exception("DeepSeek 返回为空")

        audit_passed = strategy.get("audit_passed", True)
        audit_discrepancies = strategy.get("audit_discrepancies", [])
        signal_weight = strategy.get("signal_weight", 1.0)

        if not audit_passed:
            logger.warning(f"AI 分析审计未通过，差异项: {audit_discrepancies}")

        if signal_weight < 0.5:
            logger.info(f"AI 输出 neutral 且数据有效，信号权重被降至 {signal_weight}，本次不交易")
            # 可在此处直接返回，不进行后续推送
            # return

        actual_direction = strategy.get("direction", "neutral")
        if actual_direction != "neutral":
            cluster = cg_data.get("nearest_cluster", {})
            cluster_dir = cluster.get("direction", "")
            cluster_price = float(cluster.get("price", 0)) if cluster.get("price", "N/A") != "N/A" else 0
            cluster_intensity = int(cluster.get("intensity", 0)) if cluster.get("intensity", "N/A") != "N/A" else 0

            entry_candidates = get_entry_candidates(price, atr, actual_direction, cluster, ema55, key_levels)

            # 止损计算（波动因子动态调整）
            if float(strategy.get("stop_loss", 0)) <= 0:
                base_multiplier = 2.0
                if volatility_factor > 1.3:
                    multiplier = base_multiplier * 1.3
                elif volatility_factor < 0.7:
                    multiplier = base_multiplier * 0.8
                else:
                    multiplier = base_multiplier
                if actual_direction == "long":
                    strategy["stop_loss"] = round(price - multiplier * atr, 1)
                else:
                    strategy["stop_loss"] = round(price + multiplier * atr, 1)

            stop_loss = float(strategy.get("stop_loss", 0))
            tp_candidates = get_tp_candidates(price, atr, actual_direction, cluster, stop_loss, volatility_factor)

            if float(strategy.get("take_profit", 0)) <= 0:
                if tp_candidates["rule1"]["price"] > 0 and "[盈亏比<1:1]" not in tp_candidates["rule1"]["anchor"]:
                    strategy["take_profit"] = tp_candidates["rule1"]["price"]
                    strategy["tp_anchor"] = tp_candidates["rule1"]["anchor"]
                else:
                    strategy["take_profit"] = tp_candidates["rule3"]["price"]
                    strategy["tp_anchor"] = tp_candidates["rule3"]["anchor"]

            if float(strategy.get("entry_price_low", 0)) <= 0 or float(strategy.get("entry_price_high", 0)) <= 0:
                strategy["entry_price_low"] = entry_candidates["rule3"]["low"]
                strategy["entry_price_high"] = entry_candidates["rule3"]["high"]

        is_probe = strategy.get("is_probe", False)
        probe_history.append(is_probe)

        if liq_zero_count >= 2 and strategy.get("direction") != "neutral":
            strategy["direction"] = "neutral"
            strategy["analysis_summary"] = strategy.get("analysis_summary", "") + "\n[系统] 清算数据连续缺失，自动转为观望。"

        # 使用增强版验证函数
        is_valid, error_msg = validate_strategy_enhanced(strategy, price, atr)
        if not is_valid:
            logger.warning(f"策略校验未通过: {error_msg}")

        extra = {
            "atr": atr, "funding_rate": cg_data.get("funding_rate", "N/A"),
            "oi_change": cg_data.get("oi_change_24h", "N/A"),
            "ls_ratio": cg_data.get("long_short_ratio", "N/A"),
            "cvd_signal": cvd_signal, "skew": cg_data.get("skew", "N/A"),
            "fear_greed": cg_data.get("fear_greed_index", {}).get("current", 50),
            "signal_strength": signal_strength,
            "data_source_status": data_source_status, "trend_info": trend_info,
            "volatility_factor": volatility_factor, "extreme_liq": extreme_liq,
            "is_probe": is_probe, "key_support": key_levels["support"],
            "key_resistance": key_levels["resistance"],
            "directional_scores": directional_scores,
            "audit_passed": audit_passed,
            "signal_weight": signal_weight
        }
        markdown_msg = format_strategy_message(symbol, strategy, price, extra)
        success = send_dingtalk_message(markdown_msg, f"DeepSeek策略-{symbol}")
        if success: logger.info(f"{symbol} 策略推送成功")
        else: logger.error(f"{symbol} 推送失败")
    except Exception as e:
        logger.error(f"{symbol} 策略生成失败: {e}")
        logger.error(traceback.format_exc())
        send_error_notification(symbol, str(e))
        sys.exit(1)
    logger.info(f"===== {symbol} 流程结束 =====\n")


if __name__ == "__main__":
    main()
