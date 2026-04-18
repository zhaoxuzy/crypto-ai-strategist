import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from collections import deque
from utils.logger import logger
from data_fetcher.coinglass import CoinGlassClient
from data_fetcher.okx_rest import get_current_price, calculate_atr, get_klines, calculate_ema, calculate_atr_percentile, calculate_ema_slope
from data_fetcher.macro_cache import get_macro_data
from ai_client.deepseek import build_prompt, call_deepseek, validate_strategy, calculate_signal_strength
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


def compute_macro_three_factor_score(cg: CoinGlassClient, macro_data: dict, btc_price: float) -> dict:
    bull_score = 0
    bear_score = 0
    signals = []
    
    fg = cg.get_fear_greed_index()
    fg_current = fg.get("current", 50)
    fg_prev = fg.get("prev", fg_current)  # 兜底：若无昨日值，默认等于当前值
    
    if fg_current <= 30:
        if fg_current > fg_prev:
            bull_score += 4
            signals.append(f"极恐反弹(利多, {fg_current}↑{fg_prev})")
        else:
            bull_score += 2
            signals.append(f"极恐钝化(偏多, {fg_current}≤{fg_prev})")
    elif fg_current >= 70:
        if fg_current > fg_prev:
            bear_score += 3
            signals.append(f"贪婪加速(利空, {fg_current}↑{fg_prev})")
        else:
            bear_score += 1
            signals.append(f"贪婪筑顶(偏空, {fg_current}≤{fg_prev})")
    
    premium_data = cg.get_coinbase_premium(btc_price=btc_price)
    premium_pct = premium_data.get("premium_pct", 0.0)
    
    if premium_pct > 0.15:
        bull_score += 3
        signals.append(f"Coinbase溢价(利多, {premium_pct:.2f}%)")
    elif premium_pct < -0.15:
        bear_score += 3
        signals.append(f"Coinbase折价(利空, {premium_pct:.2f}%)")
    
    stable_data = cg.get_stablecoin_market_cap_change()
    change_7d = stable_data.get("change_7d", 0.0)
    
    if change_7d > 1.0:
        bull_score += 3
        signals.append(f"稳定币增发(利多, {change_7d:.2f}%)")
    elif change_7d < -1.0:
        bear_score += 3
        signals.append(f"稳定币赎回(利空, {-change_7d:.2f}%)")
    
    total = bull_score + bear_score
    macro_component = round(total / 10 * 12) if total > 0 else 0
    macro_bull_contribution = macro_component if bull_score > bear_score else 0
    macro_bear_contribution = macro_component if bear_score > bull_score else 0
    
    return {
        "bull_score": bull_score,
        "bear_score": bear_score,
        "total": total,
        "signals": signals,
        "macro_bull_contribution": macro_bull_contribution,
        "macro_bear_contribution": macro_bear_contribution
    }


def compute_directional_scores_v2(symbol: str, coinglass_data: dict, macro_data: dict, trend_info: dict, cg: CoinGlassClient, btc_price: float) -> dict:
    bull_score = 0
    bear_score = 0
    
    # 1. 清算不对称比率（18分）
    above = float(str(coinglass_data.get("above_short_liquidation", "0")).replace(",", ""))
    below = float(str(coinglass_data.get("below_long_liquidation", "0")).replace(",", ""))
    total_liq = above + below
    if total_liq > 0:
        ratio = above / total_liq
        if ratio > 0.65:
            bear_score += 18
        elif ratio > 0.55:
            bear_score += 10
        elif ratio < 0.35:
            bull_score += 18
        elif ratio < 0.45:
            bull_score += 10
    
    # 2. 趋势强度（20分）
    trend_score = trend_info.get("score", 0)
    trend_dir = trend_info.get("direction", "neutral")
    trend_component = round(trend_score / 100 * 20)
    if trend_dir == "bull":
        bull_score += trend_component
    elif trend_dir == "bear":
        bear_score += trend_component
    
    # 3. CVD信号（15分）
    cvd = coinglass_data.get("cvd_signal", "N/A")
    if cvd == "bullish":
        bull_score += 15
    elif cvd == "slightly_bullish":
        bull_score += 8
    elif cvd == "bearish":
        bear_score += 15
    elif cvd == "slightly_bearish":
        bear_score += 8
    
    # 4. 主动买卖盘比率（10分）
    try:
        tr = float(coinglass_data.get("taker_ratio", 0.5))
        if tr >= 0.55:
            bull_score += 10
        elif tr >= 0.50:
            bull_score += 5
        elif tr <= 0.45:
            bear_score += 10
        elif tr <= 0.50:
            bear_score += 5
    except:
        pass
    
    # 5. 顶级交易员多空比（8分）
    try:
        tls = float(coinglass_data.get("top_long_short_ratio", 1.0))
        if tls < 0.7:
            bull_score += 8
        elif tls < 1.0:
            bull_score += 3
        elif tls > 2.0:
            bear_score += 8
        elif tls > 1.0:
            bear_score += 3
    except:
        pass
    
    # 6. 宏观三因子（12分）
    macro_result = compute_macro_three_factor_score(cg, macro_data, btc_price)
    bull_score += macro_result["macro_bull_contribution"]
    bear_score += macro_result["macro_bear_contribution"]
    
    # 7. ETH/BTC汇率趋势（10分）
    eth_btc = coinglass_data.get("eth_btc_ratio", {})
    if eth_btc:
        trend = eth_btc.get("trend", "neutral")
        if trend == "up":
            bull_score += 10
            bear_score = max(0, bear_score - 4)
        elif trend == "down":
            bear_score += 10
            bull_score = max(0, bull_score - 4)
    
    # 8. 清算动态信号（7分）
    liq_dynamic = coinglass_data.get("liq_dynamic_signals", [])
    dynamic_bull = 0
    dynamic_bear = 0
    for sig in liq_dynamic:
        if "清算压力偏多" in sig or "最大痛点上移" in sig:
            dynamic_bull = max(dynamic_bull, 7)
        elif "清算压力偏空" in sig or "最大痛点下移" in sig:
            dynamic_bear = max(dynamic_bear, 7)
        elif "强磁吸区" in sig:
            if "上" in sig:
                dynamic_bear = max(dynamic_bear, 4)
            else:
                dynamic_bull = max(dynamic_bull, 4)
        elif "清算堆积加速" in sig:
            if "偏多" in sig:
                dynamic_bull += 3
            else:
                dynamic_bear += 3
        elif "清算堆积衰减" in sig:
            if "偏多" in sig:
                dynamic_bull -= 3
            else:
                dynamic_bear -= 3
    bull_score += min(7, max(0, dynamic_bull))
    bear_score += min(7, max(0, dynamic_bear))
    
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
        cg_data = cg.get_all_data(symbol, current_price=price, atr=atr)
        logger.info(f"{symbol} CoinGlass 数据获取完成")
        liq_zero_count = cg.get_liq_zero_count()
        liq_warning = cg.get_liq_zero_warning()
        if liq_warning: logger.warning(liq_warning)
        data_source_status = cg.get_data_source_status()
        volatility_factor = cg.calculate_volatility_factor(symbol)
        macro = get_macro_data()

        cvd_signal = cg_data.get("cvd_signal", "neutral")
        taker_ratio = float(cg_data.get("taker_ratio", "0.5")) if cg_data.get("taker_ratio", "N/A") != "N/A" else 0.5
        liq_dynamic_signals = cg_data.get("liq_dynamic_signals", [])

        trend_info = calculate_trend_strength(klines, cvd_signal, taker_ratio, price, atr, liq_dynamic_signals)
        key_levels = get_key_levels(cg_data, ema55)
        directional_scores = compute_directional_scores_v2(symbol, cg_data, macro, trend_info, cg, price)

        above_val = float(str(cg_data.get("above_short_liquidation", "0")).replace(",", ""))
        below_val = float(str(cg_data.get("below_long_liquidation", "0")).replace(",", ""))
        extreme_liq = (above_val > EXTREME_LIQ_THRESHOLDS[symbol]) or (below_val > EXTREME_LIQ_THRESHOLDS[symbol])

        signal_strength = calculate_signal_strength(
            symbol, "long", cg_data, macro, liq_zero_count,
            cg_data.get("eth_btc_ratio"), cg.get_exchange_balances(), trend_info, extreme_liq
        )
        score = signal_strength["score"]
        if score >= 65: signal_grade = "A"
        elif score >= 40: signal_grade = "B"
        else: signal_grade = "C"

        temp_direction = trend_info.get("direction", "bull")
        if temp_direction not in ["long", "short"]:
            temp_direction = "long" if temp_direction == "bull" else "short"
        entry_candidates = get_entry_candidates(price, atr, temp_direction, cg_data.get("nearest_cluster", {}), ema55, key_levels)

        prompt = build_prompt(
            symbol=symbol, price=price, atr=atr, coinglass_data=cg_data, macro_data=macro,
            profile=profile, volatility_factor=volatility_factor, trend_info=trend_info,
            extreme_liq=extreme_liq, liq_warning=liq_warning, data_source_status=data_source_status,
            directional_scores=directional_scores, signal_grade=signal_grade,
            entry_candidates=entry_candidates
        )

        strategy = call_deepseek(prompt)
        if not strategy: raise Exception("DeepSeek 返回为空")

        actual_direction = strategy.get("direction", "neutral")
        if actual_direction != "neutral":
            cluster = cg_data.get("nearest_cluster", {})
            cluster_dir = cluster.get("direction", "")
            cluster_price = float(cluster.get("price", 0)) if cluster.get("price", "N/A") != "N/A" else 0
            cluster_intensity = int(cluster.get("intensity", 0)) if cluster.get("intensity", "N/A") != "N/A" else 0

            entry_candidates = get_entry_candidates(price, atr, actual_direction, cluster, ema55, key_levels)

            if float(strategy.get("stop_loss", 0)) <= 0:
                if actual_direction == "long":
                    strategy["stop_loss"] = round(price - 2.0 * atr, 1)
                else:
                    strategy["stop_loss"] = round(price + 2.0 * atr, 1)

            if float(strategy.get("take_profit", 0)) <= 0:
                if actual_direction == "long":
                    if cluster_intensity >= 3 and cluster_dir == "上" and cluster_price > price:
                        strategy["take_profit"] = round(cluster_price, 1)
                        strategy["tp_anchor"] = f"上方清算区 {cluster_price:.1f}"
                    else:
                        strategy["take_profit"] = round(price + 2.0 * atr * profile["tp_ratio"], 1)
                        strategy["tp_anchor"] = f"{profile['tp_ratio']:.1f}×ATR"
                else:
                    if cluster_intensity >= 3 and cluster_dir == "下" and cluster_price < price:
                        strategy["take_profit"] = round(cluster_price, 1)
                        strategy["tp_anchor"] = f"下方清算区 {cluster_price:.1f}"
                    else:
                        strategy["take_profit"] = round(price - 2.0 * atr * profile["tp_ratio"], 1)
                        strategy["tp_anchor"] = f"{profile['tp_ratio']:.1f}×ATR"

            if float(strategy.get("entry_price_low", 0)) <= 0 or float(strategy.get("entry_price_high", 0)) <= 0:
                strategy["entry_price_low"] = entry_candidates["rule3"]["low"]
                strategy["entry_price_high"] = entry_candidates["rule3"]["high"]

        is_probe = strategy.get("is_probe", False)
        probe_history.append(is_probe)

        if liq_zero_count >= 2 and strategy.get("direction") != "neutral":
            strategy["direction"] = "neutral"
            strategy["reasoning"] = "清算数据连续缺失，自动转为观望。"

        if not validate_strategy(strategy, price):
            logger.warning("策略校验未通过")

        extra = {
            "atr": atr, "funding_rate": cg_data.get("funding_rate", "N/A"),
            "oi_change": cg_data.get("oi_change_24h", "N/A"),
            "ls_ratio": cg_data.get("long_short_ratio", "N/A"),
            "cvd_signal": cvd_signal, "skew": cg_data.get("skew", "N/A"),
            "fear_greed": macro["fear_greed"]["value"], "signal_strength": signal_strength,
            "data_source_status": data_source_status, "trend_info": trend_info,
            "volatility_factor": volatility_factor, "extreme_liq": extreme_liq,
            "is_probe": is_probe, "key_support": key_levels["support"],
            "key_resistance": key_levels["resistance"],
            "directional_scores": directional_scores
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