import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from utils.logger import logger
from data_fetcher.coinglass import CoinGlassClient
from ai_client.deepseek import build_prompt, call_deepseek, validate_strategy
from notifier.dingtalk import send_dingtalk_message, format_strategy_message


def main():
    symbol = os.getenv("STRATEGY_SYMBOL", "BTC").upper()
    logger.info(f"===== 策略生成流程开始 ({symbol}) =====")
    try:
        cg = CoinGlassClient()
        data = cg.get_all_data(symbol)
        beijing_tz = timezone(timedelta(hours=8))
        data["timestamp"] = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M")
        logger.info(f"{symbol} 数据获取完成")

        prompt = build_prompt(data, symbol)
        strategy = call_deepseek(prompt)
        if not strategy:
            raise Exception("DeepSeek 返回为空")

        is_valid, error_msg = validate_strategy(strategy)
        if not is_valid:
            logger.warning(f"策略校验未通过: {error_msg}")

        markdown_msg = format_strategy_message(symbol, strategy, data)
        success = send_dingtalk_message(markdown_msg, f"DeepSeek策略-{symbol}")
        if success:
            logger.info(f"{symbol} 策略推送成功")
        else:
            logger.error(f"{symbol} 推送失败")
    except Exception as e:
        logger.error(f"{symbol} 策略生成失败: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)
    logger.info(f"===== {symbol} 流程结束 =====\n")


if __name__ == "__main__":
    main()
