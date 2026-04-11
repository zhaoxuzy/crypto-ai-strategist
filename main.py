import os
import sys
import traceback
from datetime import datetime

from utils.logger import logger
from data_fetcher.coinglass import CoinGlassClient
from data_fetcher.okx_rest import get_current_price, calculate_atr
from data_fetcher.macro_cache import get_macro_data
from ai_client.deepseek import build_prompt, call_deepseek, validate_strategy
from notifier.dingtalk import send_multi_strategies

# 支持的币种列表及对应的 OKX 永续合约 ID
SYMBOLS = [
    {"name": "BTC", "okx_id": "BTC-USDT-SWAP"},
    {"name": "ETH", "okx_id": "ETH-USDT-SWAP"},
    {"name": "SOL", "okx_id": "SOL-USDT-SWAP"},
]

def process_single_symbol(symbol_info: dict, cg: CoinGlassClient, macro_data: dict) -> tuple:
    """处理单个币种，返回 (策略字典, 价格, extra信息)"""
    symbol = symbol_info["name"]
    okx_id = symbol_info["okx_id"]
    logger.info(f"--- 开始处理 {symbol} ---")

    try:
        price = get_current_price(okx_id)
        if price <= 0:
            raise Exception("无法获取当前价格")
        atr = calculate_atr(okx_id)
        logger.info(f"{symbol} 当前价格: {price:.2f}, ATR(14): {atr:.2f}")

        cg_data = cg.get_all_data(symbol)
        logger.info(f"{symbol} CoinGlass 数据获取完成")

        prompt = build_prompt(symbol, price, atr, cg_data, macro_data)
        strategy = call_deepseek(prompt)

        if not strategy:
            raise Exception("DeepSeek 返回为空")

        if not validate_strategy(strategy, price):
            logger.warning(f"{symbol} 策略校验未通过，但仍继续")

        extra = {
            "atr": atr,
            "funding_rate": cg_data.get("funding_rate", "N/A"),
            "oi_change": cg_data.get("oi_change_24h", "N/A"),
            "ls_ratio": cg_data.get("long_short_ratio", "N/A"),
            "cvd_signal": cg_data.get("cvd_signal", "N/A"),
            "skew": cg_data.get("skew", "N/A"),
        }
        return strategy, price, extra

    except Exception as e:
        logger.error(f"{symbol} 策略生成失败: {e}")
        logger.error(traceback.format_exc())
        # 返回一个中性策略占位
        fallback_strategy = {
            "direction": "neutral",
            "confidence": "low",
            "win_rate": 0,
            "reasoning": f"数据获取失败: {str(e)}",
            "risk_note": "请检查数据源"
        }
        return fallback_strategy, 0.0, {}

def main():
    logger.info("===== 多币种策略生成流程开始 =====")
    macro_data = get_macro_data()
    logger.info(f"宏观数据: 恐惧贪婪指数 {macro_data['fear_greed']['value']}")

    cg = CoinGlassClient()
    strategies_data = []

    for sym in SYMBOLS:
        strategy, price, extra = process_single_symbol(sym, cg, macro_data)
        strategies_data.append((sym["name"], strategy, price, extra))

    # 统一推送
    success = send_multi_strategies(strategies_data, macro_data)
    if success:
        logger.info("多币种策略推送成功")
    else:
        logger.error("推送失败")

    logger.info("===== 流程结束 =====\n")

if __name__ == "__main__":
    main()
