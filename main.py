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

def calculate_trend_strength(klines: list, cvd_signal: str, taker_ratio: float, current_price: float, current_atr: float) -> dict:
    """
    计算连续趋势强度得分（0-100）及方向。
    返回：{"direction": "bull"/"bear"/"neutral", "score": 0-100, "confidence": 可信度描述, "signals": []}
    """
    if not klines or len(klines) < 55:
        return {"direction": "neutral", "score": 0, "confidence": "数据不足", "signals": []}

    ema55 = calculate_ema(klines, 55)
    ema_slope = calculate_ema_slope(klines, 55, 5)
    atr_percentile = calculate_atr_percentile(klines, current_atr, 20)

    # 基础得分 = 各条件满足情况
    score = 0
    signals = []
    direction = "neutral"

    # 1. 价格与均线关系 (0-30分)
    price_above = current_price > ema55
    if price_above:
        score += 30
        signals.append("价格>EMA55")
        direction = "bull"
    else:
        signals.append("价格<EMA55")
        direction = "bear"

    # 2. 均线斜率 (0-25分)
    if ema_slope > 2.0:
        score += 25
        signals.append("EMA斜率向上")
    elif ema_slope < -2.0:
        signals.append("EMA斜率向下")
    else:
        score += 10
        signals.append("EMA斜率走平")

    # 3. CVD信号 (0-25分)
    if cvd_signal in ["bullish", "slightly_bullish"]:
        score += 25 if cvd_signal == "bullish" else 15
        signals.append(f"CVD:{cvd_signal}")
        if direction == "neutral":
            direction = "bull"
    elif cvd_signal in ["bearish", "slightly_bearish"]:
        signals.append(f"CVD:{cvd_signal}")
        if direction == "neutral":
            direction = "bear"
    else:
        score += 5
        signals.append("CVD:neutral")

    # 4. 主动买卖盘 (0-20分)
    if taker_ratio > 0.55:
        score += 20
        signals.append(f"主动买盘({taker_ratio:.2f})")
        if direction == "neutral":
            direction = "bull"
    elif taker_ratio < 0.45:
        signals.append(f"主动卖盘({taker_ratio:.2f})")
        if direction == "neutral":
            direction = "bear"
    else:
        score += 10
        signals.append("主动买卖均衡")

    # 低波动惩罚：ATR百分位<30%时，趋势强度降权
    if atr_percentile < 30:
        score = int(score * 0.6)
        signals.append(f"低波动惩罚(ATR百分位{atr_percentile:.0f}%)")

    # 限制范围
    score = max(0, min(100, score))

    # 可信度描述
    if score >= 70:
        confidence = "高"
    elif score >= 40:
        confidence = "中"
    else:
        confidence = "低"

    # 方向校正：如果方向与主要得分矛盾，设为neutral
    if direction == "bull" and current_price < ema55 and cvd_signal not in ["bullish", "slightly_bullish"]:
        direction = "neutral"
    elif direction == "bear" and current_price > ema55 and cvd_signal not in ["bearish", "slightly_bearish"]:
        direction = "neutral"

    return {
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "signals": signals,
        "ema55": ema55,
        "ema_slope": ema_slope,
        "atr_percentile": atr_percentile
    }

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
    if symbol not in SYMBOL_MAP:
        symbol = "BTC"
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
        taker_ratio_str = cg_data.get("taker_ratio", "0.5")
        try:
            taker_ratio = float(taker_ratio_str)
        except:
            taker_ratio = 0.5

        trend_info = calculate_trend_strength(klines, cvd_signal, taker_ratio, price, atr)
        trend_direction = trend_info["direction"]
        trend_score = trend_info["score"]
        logger.info(f"趋势强度: 方向={trend_direction}, 得分={trend_score}/100, 可信度={trend_info['confidence']}")

        above_val = float(str(cg_data.get("above_short_liquidation", "0")).replace(",", ""))
        below_val = float(str(cg_data.get("below_long_liquidation", "0")).replace(",", ""))
        extreme_liq = False
        if symbol.upper() == "BTC":
            extreme_liq = (above_val > 200_000_000) or (below_val > 200_000_000)
        elif symbol.upper() == "ETH":
            extreme_liq = (above_val > 80_000_000) or (below_val > 80_000_000)

        prompt = build_prompt(
            symbol=symbol, price=price, atr=atr, coinglass_data=cg_data, macro_data=macro,
            profile=profile, volatility_factor=volatility_factor, trend_info=trend_info,
            extreme_liq=extreme_liq, liq_warning=liq_warning, data_source_status=data_source_status
        )
        strategy = call_deepseek(prompt)
        if not strategy: raise Exception("DeepSeek 返回为空")

        is_probe = strategy.get("is_probe", False)
        probe_history.append(is_probe)
        probe_ratio = sum(probe_history) / len(probe_history) if probe_history else 0
        if is_probe and probe_ratio > 0.3:
            strategy["risk_note"] = strategy.get("risk_note", "") + " 系统告警：试探信号比例过高，请谨慎参考。"

        if liq_zero_count >= 2 and strategy.get("direction") != "neutral":
            strategy["direction"] = "neutral"
            strategy["confidence"] = "low"
            strategy["reasoning"] = "清算数据连续缺失，无法构建有效策略，自动转为观望。"

        eth_btc_data = cg.get_eth_btc_ratio()
        balance_data = cg.get_exchange_balances()

        signal_strength = calculate_signal_strength(
            symbol, strategy["direction"], cg_data, macro, liq_zero_count,
            eth_btc_data, balance_data, trend_info, extreme_liq
        )
        strategy["win_rate"] = signal_strength["win_rate"]

        if strategy.get("direction") != "neutral" and strategy["win_rate"] < MIN_WIN_RATE:
            original_direction = strategy["direction"]
            original_win_rate = strategy["win_rate"]
            strategy["direction"] = "neutral"
            strategy["confidence"] = "low"
            strategy["reasoning"] = f"策略胜率({original_win_rate}%)低于最低阈值({MIN_WIN_RATE}%)，自动转为观望。原方向：{original_direction}"
            strategy["win_rate"] = 0
            logger.info(f"{symbol} 策略胜率{original_win_rate}%低于{MIN_WIN_RATE}%，已转为neutral")

        if not validate_strategy(strategy, price): logger.warning("策略校验未通过")
        extra = {
            "atr": atr,
            "funding_rate": cg_data.get("funding_rate", "N/A"),
            "oi_change": cg_data.get("oi_change_24h", "N/A"),
            "ls_ratio": cg_data.get("long_short_ratio", "N/A"),
            "cvd_signal": cg_data.get("cvd_signal", "N/A"),
            "skew": cg_data.get("skew", "N/A"),
            "fear_greed": macro["fear_greed"]["value"],
            "signal_strength": signal_strength,
            "data_source_status": data_source_status,
            "trend_info": trend_info,
            "volatility_factor": volatility_factor,
            "extreme_liq": extreme_liq,
            "is_probe": is_probe
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
