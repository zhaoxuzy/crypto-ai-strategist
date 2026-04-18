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
    "BTC": {"stop_multiplier": 1.5, "tp1_ratio": 2.0, "tp2_ratio": 3.5, "volatility_discount": 0.8},
    "ETH": {"stop_multiplier": 1.8, "tp1_ratio": 2.0, "tp2_ratio": 3.5, "volatility_discount": 0.7},
    "SOL": {"stop_multiplier": 2.5, "tp1_ratio": 2.0, "tp2_ratio": 4.0, "volatility_discount": 0.6}
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


def compute_directional_scores(symbol: str, coinglass_data: dict, macro_data: dict, trend_info: dict) -> dict:
    bull_score, bear_score = 0, 0
    above = float(str(coinglass_data.get("above_short_liquidation", "0")).replace(",", ""))
    below = float(str(coinglass_data.get("below_long_liquidation", "0")).replace(",", ""))
    total = above + below
    if total > 0:
        ratio_above = above / total
        if ratio_above > 0.6:
            bear_score += 25
        elif ratio_above < 0.4:
            bull_score += 25
    cvd = coinglass_data.get("cvd_signal", "N/A")
    if cvd in ["bearish", "slightly_bearish"]:
        bear_score += 20
    elif cvd in ["bullish", "slightly_bullish"]:
        bull_score += 20
    try:
        tr = float(coinglass_data.get("taker_ratio", 0.5))
        if tr < 0.45:
            bear_score += 15
        elif tr > 0.55:
            bull_score += 15
    except:
        pass
    try:
        tls = float(coinglass_data.get("top_long_short_ratio", 1.0))
        if tls > 2.0:
            bear_score += 15
        elif tls < 0.7:
            bull_score += 15
    except:
        pass
    fg = int(macro_data.get("fear_greed", {}).get("value", 50))
    if fg < 30:
        bull_score += 10
    elif fg > 70:
        bear_score += 10
    trend_dir = trend_info.get("direction", "neutral")
    trend_score = trend_info.get("score", 0)
    if trend_dir == "bull":
        bull_score += int(trend_score / 10)
    elif trend_dir == "bear":
        bear_score += int(trend_score / 10)

    # ETH/BTC 汇率趋势
    eth_btc = coinglass_data.get("eth_btc_ratio", {})
    if eth_btc:
        trend = eth_btc.get("trend", "neutral")
        if trend == "up":
            bull_score += 8
            bear_score = max(0, bear_score - 4)
        elif trend == "down":
            bear_score += 8
            bull_score = max(0, bull_score - 4)

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

        cvd_signal = cg_data.get("cvd_signal", "neutral")
        taker_ratio = float(cg_data.get("taker_ratio", "0.5")) if cg_data.get("taker_ratio", "N/A") != "N/A" else 0.5
        liq_dynamic_signals = cg_data.get("liq_dynamic_signals", [])

        trend_info = calculate_trend_strength(klines, cvd_signal, taker_ratio, price, atr, liq_dynamic_signals)
        key_levels = get_key_levels(cg_data, ema55)
        directional_scores = compute_directional_scores(symbol, cg_data, macro, trend_info)

        above_val = float(str(cg_data.get("above_short_liquidation", "0")).replace(",", ""))
        below_val = float(str(cg_data.get("below_long_liquidation", "0")).replace(",", ""))
        extreme_liq = (above_val > EXTREME_LIQ_THRESHOLDS[symbol]) or (below_val > EXTREME_LIQ_THRESHOLDS[symbol])

        signal_strength = calculate_signal_strength(
            symbol, "long", cg_data, macro, liq_zero_count,
            cg.get_eth_btc_ratio(), cg.get_exchange_balances(), trend_info, extreme_liq
        )
        score = signal_strength["score"]
        if score >= 65: signal_grade = "A"
        elif score >= 40: signal_grade = "B"
        else: signal_grade = "C"

        # 构建 Prompt，不再传入固定止损止盈候选值
        prompt = build_prompt(
            symbol=symbol, price=price, atr=atr, coinglass_data=cg_data, macro_data=macro,
            profile=profile, volatility_factor=volatility_factor, trend_info=trend_info,
            extreme_liq=extreme_liq, liq_warning=liq_warning, data_source_status=data_source_status,
            directional_scores=directional_scores, signal_grade=signal_grade,
            # 以下参数不再使用，传空值保持兼容
            stop_loss_rule2=0.0, stop_loss_rule3=0.0, tp1=0.0, tp2=0.0, tp1_anchor="", tp2_anchor=""
        )

        strategy = call_deepseek(prompt)
        if not strategy: raise Exception("DeepSeek 返回为空")

        actual_direction = strategy.get("direction", "neutral")
        # 如果AI未提供止损止盈，则根据清算结构补充（兜底）
        if actual_direction != "neutral":
            cluster = cg_data.get("nearest_cluster", {})
            cluster_dir = cluster.get("direction", "")
            cluster_price = float(cluster.get("price", 0)) if cluster.get("price", "N/A") != "N/A" else 0
            cluster_intensity = int(cluster.get("intensity", 0)) if cluster.get("intensity", "N/A") != "N/A" else 0
            max_pain = float(cg_data.get("max_pain_price", 0)) if cg_data.get("max_pain_price", "N/A") != "N/A" else 0

            # 兜底止损
            if float(strategy.get("stop_loss", 0)) <= 0:
                if actual_direction == "long":
                    strategy["stop_loss"] = round(key_levels["support"] * 0.998, 1)
                else:
                    strategy["stop_loss"] = round(key_levels["resistance"] * 1.002, 1)

            # 兜底止盈1
            if float(strategy.get("take_profit_1", 0)) <= 0:
                if actual_direction == "long":
                    if cluster_intensity >= 3 and cluster_dir == "上" and cluster_price > price:
                        strategy["take_profit_1"] = round(cluster_price, 1)
                        strategy["tp1_anchor"] = f"上方清算区 {cluster_price:.1f}"
                    else:
                        strategy["take_profit_1"] = round(price + 2.0 * atr, 1)
                        strategy["tp1_anchor"] = "2×ATR"
                else:
                    if cluster_intensity >= 3 and cluster_dir == "下" and cluster_price < price:
                        strategy["take_profit_1"] = round(cluster_price, 1)
                        strategy["tp1_anchor"] = f"下方清算区 {cluster_price:.1f}"
                    else:
                        strategy["take_profit_1"] = round(price - 2.0 * atr, 1)
                        strategy["tp1_anchor"] = "2×ATR"

            # 兜底止盈2
            if float(strategy.get("take_profit_2", 0)) <= 0:
                if max_pain > 0:
                    strategy["take_profit_2"] = round(max_pain, 1)
                    strategy["tp2_anchor"] = "清算最大痛点"
                else:
                    strategy["take_profit_2"] = round(price + 3.5 * atr, 1) if actual_direction == "long" else round(price - 3.5 * atr, 1)
                    strategy["tp2_anchor"] = "3.5×ATR"

        is_probe = strategy.get("is_probe", False)
        probe_history.append(is_probe)

        if liq_zero_count >= 2 and strategy.get("direction") != "neutral":
            strategy["direction"] = "neutral"
            strategy["confidence"] = "low"
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