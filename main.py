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
    "BTC": {"base_win_rate": 50, "max_win_rate": 85, "stop_multiplier": 1.5, "tp1_ratio": 1.5, "tp2_ratio": 2.5, "volatility_discount": 0.8, "min_profit_pct": 0.0025, "min_profit_atr_mult": 0.4, "tp2_layer_atr_mult": 0.2, "signals": {"liquidation": {"weight": 10, "reliable": True}, "funding_rate": {"weight": 10, "reliable": True}, "top_trader": {"weight": 10, "reliable": True}, "cvd": {"weight": 10, "reliable": True}, "fear_greed": {"weight": 10, "reliable": True}, "option_pain": {"weight": 0, "reliable": True}}},
    "ETH": {"base_win_rate": 48, "max_win_rate": 80, "stop_multiplier": 1.8, "tp1_ratio": 1.8, "tp2_ratio": 3.0, "volatility_discount": 0.7, "min_profit_pct": 0.003, "min_profit_atr_mult": 0.5, "tp2_layer_atr_mult": 0.3, "signals": {"liquidation": {"weight": 12, "reliable": True}, "funding_rate": {"weight": 10, "reliable": True}, "top_trader": {"weight": 10, "reliable": True}, "cvd": {"weight": 12, "reliable": True}, "fear_greed": {"weight": 8, "reliable": True}, "option_pain": {"weight": 0, "reliable": True}}},
    "SOL": {"base_win_rate": 45, "max_win_rate": 75, "stop_multiplier": 2.5, "tp1_ratio": 2.0, "tp2_ratio": 3.5, "volatility_discount": 0.6, "min_profit_pct": 0.005, "min_profit_atr_mult": 0.8, "tp2_layer_atr_mult": 0.5, "signals": {"liquidation": {"weight": 20, "reliable": True}, "funding_rate": {"weight": 10, "reliable": True}, "top_trader": {"weight": 0, "reliable": False}, "cvd": {"weight": 15, "reliable": True}, "fear_greed": {"weight": 10, "reliable": True}, "option_pain": {"weight": 0, "reliable": False}}}
}

MIN_WIN_RATE = 50
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

    # ----- 新增：清算动态信号融入趋势得分 -----
    if liq_dynamic_signals:
        for sig in liq_dynamic_signals:
            if "清算压力偏空" in sig:
                score += 10
                signals.append(sig)
                if direction == "neutral":
                    direction = "bear"
            elif "清算压力偏多" in sig:
                score += 10
                signals.append(sig)
                if direction == "neutral":
                    direction = "bull"
            elif "最大痛点上移" in sig:
                score += 8
                signals.append(sig)
                if direction == "neutral":
                    direction = "bull"
            elif "最大痛点下移" in sig:
                score += 8
                signals.append(sig)
                if direction == "neutral":
                    direction = "bear"
            elif "强磁吸区" in sig:
                score += 6
                signals.append(sig)
            elif "清算堆积加速" in sig:
                score += 5
                signals.append(sig)
            elif "清算堆积衰减" in sig:
                score -= 3
                signals.append(sig)

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

def check_momentum_override(klines: list, cvd_signal: str, taker_ratio: float, current_price: float) -> dict:
    if not klines or len(klines) < 5: return {"active": False}
    ema55 = calculate_ema(klines, 55)
    if ema55 == 0: return {"active": False}
    price_below = current_price < ema55
    cvd_bearish = cvd_signal in ["bearish", "slightly_bearish"]
    taker_bearish = taker_ratio < 0.45
    price_above = current_price > ema55
    cvd_bullish = cvd_signal in ["bullish", "slightly_bullish"]
    taker_bullish = taker_ratio > 0.55
    if (price_below and cvd_bearish and not taker_bullish) or (price_below and taker_bearish and not cvd_bullish) or (cvd_bearish and taker_bearish and not price_above):
        return {"active": True, "direction": "short", "cvd": cvd_signal, "taker": taker_ratio}
    elif (price_above and cvd_bullish and not taker_bearish) or (price_above and taker_bullish and not cvd_bearish) or (cvd_bullish and taker_bullish and not price_below):
        return {"active": True, "direction": "long", "cvd": cvd_signal, "taker": taker_ratio}
    return {"active": False}

def compute_directional_scores(symbol: str, coinglass_data: dict, macro_data: dict, trend_info: dict) -> dict:
    bull_score, bear_score = 0, 0
    above = float(str(coinglass_data.get("above_short_liquidation", "0")).replace(",", ""))
    below = float(str(coinglass_data.get("below_long_liquidation", "0")).replace(",", ""))
    if above + below > 0:
        if above > below * 1.3: bear_score += 30
        elif below > above * 1.3: bull_score += 30
    cvd = coinglass_data.get("cvd_signal", "N/A")
    if cvd in ["bearish", "slightly_bearish"]: bear_score += 25
    elif cvd in ["bullish", "slightly_bullish"]: bull_score += 25
    try:
        tr = float(coinglass_data.get("taker_ratio", 0.5))
        if tr < 0.45: bear_score += 15
        elif tr > 0.55: bull_score += 15
    except: pass
    try:
        tls = float(coinglass_data.get("top_long_short_ratio", 1.0))
        if tls > 2.0: bear_score += 20
        elif tls < 0.7: bull_score += 20
    except: pass
    fg = int(macro_data.get("fear_greed", {}).get("value", 50))
    if fg < 30: bull_score += 10
    elif fg > 70: bear_score += 10
    return {"bull": bull_score, "bear": bear_score}

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
        klines = get_klines(okx_inst_id, "1H", 70)
        atr = calculate_atr(okx_inst_id)
        ema55 = calculate_ema(klines, 55)
        logger.info(f"{symbol} 当前价格: {price:.2f}, ATR(14): {atr:.2f}, EMA55: {ema55:.1f}")
        cg = CoinGlassClient()
        cg_data = cg.get_all_data(symbol, current_price=price, atr=atr)
        logger.info(f"{symbol} CoinGlass 数据获取完成")
        liq_zero_count = cg.get_liq_zero_count()
        liq_warning = cg.get_liq_zero_warning()
        if liq_warning: logger.warning(liq_warning)
        data_source_status = cg.get_data_source_status()
        volatility_factor = cg.calculate_volatility_factor(symbol)
        macro = get_macro_data()

        if volatility_factor > 1.5:
            profile['stop_multiplier'] = profile.get('stop_multiplier', 1.5) * 1.5
            profile['tp1_ratio'] = profile.get('tp1_ratio', 1.5) * 1.3
        elif volatility_factor < 0.7:
            profile['stop_multiplier'] = profile.get('stop_multiplier', 1.5) * 0.8
            profile['tp1_ratio'] = profile.get('tp1_ratio', 1.5) * 0.8

        cvd_signal = cg_data.get("cvd_signal", "neutral")
        taker_ratio = float(cg_data.get("taker_ratio", "0.5")) if cg_data.get("taker_ratio", "N/A") != "N/A" else 0.5

        # 获取清算动态信号列表
        liq_dynamic_signals = cg_data.get("liq_dynamic_signals", [])
        trend_info = calculate_trend_strength(klines, cvd_signal, taker_ratio, price, atr, liq_dynamic_signals)
        momentum_override = check_momentum_override(klines, cvd_signal, taker_ratio, price)
        key_levels = get_key_levels(cg_data, ema55)
        directional_scores = compute_directional_scores(symbol, cg_data, macro, trend_info)

        near_key_level = False
        if trend_info["direction"] == "bull" and key_levels["resistance"] > 0:
            if abs(price - key_levels["resistance"]) < 0.3 * atr: near_key_level = True
        elif trend_info["direction"] == "bear" and key_levels["support"] > 0:
            if abs(price - key_levels["support"]) < 0.3 * atr: near_key_level = True

        above_val = float(str(cg_data.get("above_short_liquidation", "0")).replace(",", ""))
        below_val = float(str(cg_data.get("below_long_liquidation", "0")).replace(",", ""))
        extreme_liq = (above_val > EXTREME_LIQ_THRESHOLDS[symbol]) or (below_val > EXTREME_LIQ_THRESHOLDS[symbol])

        prompt = build_prompt(
            symbol=symbol, price=price, atr=atr, coinglass_data=cg_data, macro_data=macro,
            profile=profile, volatility_factor=volatility_factor, trend_info=trend_info,
            extreme_liq=extreme_liq, liq_warning=liq_warning, data_source_status=data_source_status,
            momentum_override=momentum_override, key_levels=key_levels, near_key_level=near_key_level,
            directional_scores=directional_scores
        )
        strategy = call_deepseek(prompt, momentum_override, extreme_liq)
        if not strategy: raise Exception("DeepSeek 返回为空")

        is_probe = strategy.get("is_probe", False)
        probe_history.append(is_probe)
        if is_probe and sum(probe_history)/len(probe_history) > 0.3:
            strategy["risk_note"] = strategy.get("risk_note", "") + " 系统告警：试探信号比例过高，请谨慎参考。"

        if liq_zero_count >= 2 and strategy.get("direction") != "neutral":
            strategy["direction"] = "neutral"
            strategy["confidence"] = "low"
            strategy["reasoning"] = "清算数据连续缺失，自动转为观望。"

        signal_strength = calculate_signal_strength(symbol, strategy["direction"], cg_data, macro, liq_zero_count, cg.get_eth_btc_ratio(), cg.get_exchange_balances(), trend_info, extreme_liq)
        strategy["win_rate"] = signal_strength["win_rate"]

        if strategy.get("direction") != "neutral" and strategy["win_rate"] < MIN_WIN_RATE:
            original_reasoning = strategy.get("reasoning", "")
            strategy["direction"] = "neutral"
            strategy["confidence"] = "low"
            strategy["reasoning"] = f"{original_reasoning}\n\n【系统风控拦截】预估胜率{strategy['win_rate']}%低于阈值{MIN_WIN_RATE}%，强制转为观望。"

        if not validate_strategy(strategy, price): logger.warning("策略校验未通过")
        extra = {
            "atr": atr, "funding_rate": cg_data.get("funding_rate", "N/A"), "oi_change": cg_data.get("oi_change_24h", "N/A"),
            "ls_ratio": cg_data.get("long_short_ratio", "N/A"), "cvd_signal": cvd_signal, "skew": cg_data.get("skew", "N/A"),
            "fear_greed": macro["fear_greed"]["value"], "signal_strength": signal_strength, "data_source_status": data_source_status,
            "trend_info": trend_info, "volatility_factor": volatility_factor, "extreme_liq": extreme_liq, "is_probe": is_probe,
            "momentum_active": momentum_override.get("active", False), "near_key_level": near_key_level,
            "key_support": key_levels["support"], "key_resistance": key_levels["resistance"]
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