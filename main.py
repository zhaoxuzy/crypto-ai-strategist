import os
import sys
import traceback
from datetime import datetime

from utils.logger import logger
from data_fetcher.coinglass import CoinGlassClient
from data_fetcher.okx_rest import get_current_price, calculate_atr
from data_fetcher.macro_cache import get_macro_data
from ai_client.deepseek import build_prompt, call_deepseek, validate_strategy
from notifier.dingtalk import send_dingtalk_message, format_strategy_message

SYMBOL_MAP = {
    "BTC": "BTC-USDT-SWAP",
    "ETH": "ETH-USDT-SWAP",
    "SOL": "SOL-USDT-SWAP",
}

def main():
    symbol = os.getenv("STRATEGY_SYMBOL", "BTC").upper()
    if symbol not in SYMBOL_MAP:
        logger.error(f"不支持的币种: {symbol}，将使用 BTC")
        symbol = "BTC"

    okx_inst_id = SYMBOL_MAP[symbol]
    logger.info(f"===== 策略生成流程开始 ({symbol}) =====")

    try:
        price = get_current_price(okx_inst_id)
        if price <= 0:
            raise Exception("无法获取当前价格")
        atr = calculate_atr(okx_inst_id)
        logger.info(f"{symbol} 当前价格: {price:.2f}, ATR(14): {atr:.2f}")

        cg = CoinGlassClient()
        cg_data = cg.get_all_data(symbol, current_price=price)
        logger.info(f"{symbol} CoinGlass 数据获取完成")

        macro = get_macro_data()
        logger.info(f"宏观数据: 恐惧贪婪指数 {macro['fear_greed']['value']}")

        prompt = build_prompt(symbol, price, atr, cg_data, macro)
        strategy = call_deepseek(prompt)

        if not strategy:
            raise Exception("DeepSeek 返回为空")

        if not validate_strategy(strategy, price):
            logger.warning("策略校验未通过，但仍尝试推送")

        extra = {
            "atr": atr,
            "funding_rate": cg_data.get("funding_rate", "N/A"),
            "oi_change": cg_data.get("oi_change_24h", "N/A"),
            "ls_ratio": cg_data.get("long_short_ratio", "N/A"),
            "cvd_signal": cg_data.get("cvd_signal", "N/A"),
            "skew": cg_data.get("skew", "N/A"),
            "fear_greed": macro["fear_greed"]["value"],
        }

        markdown_msg = format_strategy_message(symbol, strategy, price, extra)
        success = send_dingtalk_message(markdown_msg, f"{symbol}策略推送")
        if success:
            logger.info(f"{symbol} 策略推送成功")
        else:
            logger.error(f"{symbol} 推送失败")

    except Exception as e:
        logger.error(f"{symbol} 策略生成失败: {e}")
        logger.error(traceback.format_exc())
        error_msg = f"## ❌ [{symbol}] 策略生成失败 🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n> 错误信息：{str(e)}\n\n请检查数据源或稍后重试。"
        send_dingtalk_message(error_msg, f"{symbol}策略生成异常")
        sys.exit(1)

    logger.info(f"===== {symbol} 流程结束 =====\n")

if __name__ == "__main__":
    main()
