import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from utils.logger import logger
from data_fetcher.coinglass import CoinGlassClient
from data_fetcher.okx_rest import get_current_price, calculate_atr, get_klines
from data_fetcher.macro_cache import get_macro_data
from ai_client.deepseek import build_prompt, call_deepseek, validate_strategy, calculate_signal_strength
from notifier.dingtalk import send_dingtalk_message, format_strategy_message

SYMBOL_MAP = {"BTC": "BTC-USDT-SWAP", "ETH": "ETH-USDT-SWAP", "SOL": "SOL-USDT-SWAP"}
STRATEGY_PROFILES = {
    "BTC": {"base_win_rate": 50, "max_win_rate": 85, "base_position": 0.25, "max_position": 0.50, "stop_multiplier": 1.5, "tp1_ratio": 1.5, "tp2_ratio": 2.5, "volatility_discount": 0.8, "min_profit_pct": 0.0025, "min_profit_atr_mult": 0.4, "tp2_layer_atr_mult": 0.2, "signals": {"liquidation": {"weight": 10, "reliable": True}, "funding_rate": {"weight": 10, "reliable": True}, "top_trader": {"weight": 10, "reliable": True}, "cvd": {"weight": 10, "reliable": True}, "fear_greed": {"weight": 10, "reliable": True}, "option_pain": {"weight": 0, "reliable": True}}},
    "ETH": {"base_win_rate": 48, "max_win_rate": 80, "base_position": 0.20, "max_position": 0.40, "stop_multiplier": 1.8, "tp1_ratio": 1.8, "tp2_ratio": 3.0, "volatility_discount": 0.7, "min_profit_pct": 0.003, "min_profit_atr_mult": 0.5, "tp2_layer_atr_mult": 0.3, "signals": {"liquidation": {"weight": 12, "reliable": True}, "funding_rate": {"weight": 10, "reliable": True}, "top_trader": {"weight": 10, "reliable": True}, "cvd": {"weight": 12, "reliable": True}, "fear_greed": {"weight": 8, "reliable": True}, "option_pain": {"weight": 0, "reliable": True}}},
    "SOL": {"base_win_rate": 45, "max_win_rate": 75, "base_position": 0.15, "max_position": 0.30, "stop_multiplier": 2.5, "tp1_ratio": 2.0, "tp2_ratio": 3.5, "volatility_discount": 0.6, "min_profit_pct": 0.005, "min_profit_atr_mult": 0.8, "tp2_layer_atr_mult": 0.5, "signals": {"liquidation": {"weight": 20, "reliable": True}, "funding_rate": {"weight": 10, "reliable": True}, "top_trader": {"weight": 0, "reliable": False}, "cvd": {"weight": 15, "reliable": True}, "fear_greed": {"weight": 10, "reliable": True}, "option_pain": {"weight": 0, "reliable": False}}}
}

MIN_WIN_RATE = 50

def get_simple_direction(symbol: str) -> str:
    """获取指定币种的简化方向（仅用于共振判断）"""
    try:
        okx_id = SYMBOL_MAP.get(symbol.upper())
        if not okx_id:
            return "neutral"
        cg = CoinGlassClient()
        price = get_current_price(okx_id)
        if price <= 0:
            return "neutral"
        cg_data = cg.get_all_data(symbol, current_price=price)
        above = cg_data.get("above_short_liquidation", "0")
        below = cg_data.get("below_long_liquidation", "0")
        try:
            above_val = float(above.replace(",", "")) if isinstance(above, str) else float(above)
            below_val = float(below.replace(",", "")) if isinstance(below, str) else float(below)
            if above_val > below_val * 1.2:
                return "long"
            elif below_val > above_val * 1.2:
                return "short"
        except:
            pass
        top_ls = cg_data.get("top_long_short_ratio", "N/A")
        try:
            tls = float(top_ls)
            if tls < 0.7:
                return "long"
            elif tls > 2.0:
                return "short"
        except:
            pass
        return "neutral"
    except Exception as e:
        logger.warning(f"获取{symbol}简化方向失败: {e}")
        return "neutral"

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
    symbol = os.getenv("STRATEGY_SYMBOL", "BTC").upper()
    if symbol not in SYMBOL_MAP:
        symbol = "BTC"
    profile = STRATEGY_PROFILES.get(symbol, STRATEGY_PROFILES["BTC"])
    okx_inst_id = SYMBOL_MAP[symbol]

    # 获取 BTC 和 ETH 的简化方向（用于双币共振）
    btc_direction = "neutral"
    eth_direction = "neutral"
    if symbol.upper() in ("BTC", "ETH"):
        try:
            btc_direction = get_simple_direction("BTC")
            eth_direction = get_simple_direction("ETH")
            logger.info(f"双币共振方向: BTC={btc_direction}, ETH={eth_direction}")
        except Exception as e:
            logger.warning(f"获取双币方向失败: {e}")

    logger.info(f"===== 策略生成流程开始 ({symbol}) =====")
    try:
        price = get_current_price(okx_inst_id)
        if price <= 0: raise Exception("无法获取当前价格")
        klines = get_klines(okx_inst_id, "1H", 30)
        atr = calculate_atr(okx_inst_id)
        logger.info(f"{symbol} 当前价格: {price:.2f}, ATR(14): {atr:.2f}")
        cg = CoinGlassClient()
        cg_data = cg.get_all_data(symbol, current_price=price, atr=atr)
        logger.info(f"{symbol} CoinGlass 数据获取完成")
        liq_zero_count = cg.get_liq_zero_count()
        liq_warning = cg.get_liq_zero_warning()
        if liq_warning: logger.warning(liq_warning)
        data_source_status = cg.get_data_source_status()
        volatility_factor = cg.calculate_volatility_factor(symbol)
        market_regime = cg.get_market_regime_from_klines(klines, price, atr) if hasattr(cg, 'get_market_regime_from_klines') else {"regime": "range", "details": {"reason": "默认"}}
        macro = get_macro_data()
        prompt = build_prompt(symbol=symbol, price=price, atr=atr, coinglass_data=cg_data, macro_data=macro, profile=profile, volatility_factor=volatility_factor, market_regime=market_regime, liq_warning=liq_warning, data_source_status=data_source_status)
        strategy = call_deepseek(prompt)
        if not strategy: raise Exception("DeepSeek 返回为空")

        if liq_zero_count >= 2 and strategy.get("direction") != "neutral":
            strategy["direction"] = "neutral"
            strategy["confidence"] = "low"
            strategy["reasoning"] = "清算数据连续缺失，无法构建有效策略，自动转为观望。"

        signal_strength = calculate_signal_strength(symbol, strategy["direction"], cg_data, macro, liq_zero_count, btc_direction, eth_direction)
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
        extra = {"atr": atr, "funding_rate": cg_data.get("funding_rate", "N/A"), "oi_change": cg_data.get("oi_change_24h", "N/A"), "ls_ratio": cg_data.get("long_short_ratio", "N/A"), "cvd_signal": cg_data.get("cvd_signal", "N/A"), "skew": cg_data.get("skew", "N/A"), "fear_greed": macro["fear_greed"]["value"], "signal_strength": signal_strength, "data_source_status": data_source_status}
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
