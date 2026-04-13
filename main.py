import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

from utils.logger import logger
from data_fetcher.coinglass import CoinGlassClient
from data_fetcher.okx_rest import get_current_price, calculate_atr
from data_fetcher.macro_cache import get_macro_data
from ai_client.deepseek import build_prompt, call_deepseek, validate_strategy, calculate_win_rate, calculate_signal_strength
from notifier.dingtalk import send_dingtalk_message, format_strategy_message

SYMBOL_MAP = {
    "BTC": "BTC-USDT-SWAP",
    "ETH": "ETH-USDT-SWAP",
    "SOL": "SOL-USDT-SWAP",
}

STRATEGY_PROFILES = {
    "BTC": {
        "base_win_rate": 50,
        "max_win_rate": 85,
        "base_position": 0.25,
        "max_position": 0.50,
        "stop_multiplier": 1.5,
        "tp1_ratio": 1.5,
        "tp2_ratio": 2.5,
        "volatility_discount": 0.8,
        "signals": {
            "liquidation": {"weight": 10, "reliable": True},
            "funding_rate": {"weight": 10, "reliable": True},
            "top_trader": {"weight": 10, "reliable": True},
            "cvd": {"weight": 10, "reliable": True},
            "fear_greed": {"weight": 10, "reliable": True},
            "option_pain": {"weight": 0, "reliable": True},
        }
    },
    "ETH": {
        "base_win_rate": 48,
        "max_win_rate": 80,
        "base_position": 0.20,
        "max_position": 0.40,
        "stop_multiplier": 1.8,
        "tp1_ratio": 1.8,
        "tp2_ratio": 3.0,
        "volatility_discount": 0.7,
        "signals": {
            "liquidation": {"weight": 12, "reliable": True},
            "funding_rate": {"weight": 10, "reliable": True},
            "top_trader": {"weight": 10, "reliable": True},
            "cvd": {"weight": 12, "reliable": True},
            "fear_greed": {"weight": 8, "reliable": True},
            "option_pain": {"weight": 0, "reliable": True},
        }
    },
    "SOL": {
        "base_win_rate": 45,
        "max_win_rate": 75,
        "base_position": 0.15,
        "max_position": 0.30,
        "stop_multiplier": 2.2,
        "tp1_ratio": 2.0,
        "tp2_ratio": 3.5,
        "volatility_discount": 0.6,
        "signals": {
            "liquidation": {"weight": 20, "reliable": True},
            "funding_rate": {"weight": 10, "reliable": True},
            "top_trader": {"weight": 0, "reliable": False},
            "cvd": {"weight": 15, "reliable": True},
            "fear_greed": {"weight": 10, "reliable": True},
            "option_pain": {"weight": 0, "reliable": False},
        }
    }
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

---
*系统将在下次定时任务时自动重试。*
"""
    send_dingtalk_message(markdown, f"DeepSeek策略异常-{symbol}")

def main():
    symbol = os.getenv("STRATEGY_SYMBOL", "BTC").upper()
    if symbol not in SYMBOL_MAP:
        logger.error(f"不支持的币种: {symbol}，将使用 BTC")
        symbol = "BTC"

    profile = STRATEGY_PROFILES.get(symbol, STRATEGY_PROFILES["BTC"])
    okx_inst_id = SYMBOL_MAP[symbol]

    logger.info(f"===== 策略生成流程开始 ({symbol}) =====")

    price = 0.0
    atr = 0.0
    cg_data = {}
    macro = {}

    try:
        price = get_current_price(okx_inst_id)
        if price <= 0:
            raise Exception("无法获取当前价格")
        atr = calculate_atr(okx_inst_id)
        logger.info(f"{symbol} 当前价格: {price:.2f}, ATR(14): {atr:.2f}")

        cg = CoinGlassClient()
        cg_data = cg.get_all_data(symbol, current_price=price)
        logger.info(f"{symbol} CoinGlass 数据获取完成")

        volatility_factor = cg.calculate_volatility_factor(symbol)
        logger.info(f"{symbol} 波动率因子: {volatility_factor:.2f}")

        macro = get_macro_data()
        logger.info(f"宏观数据: 恐惧贪婪指数 {macro['fear_greed']['value']}")

        prompt = build_prompt(
            symbol=symbol,
            price=price,
            atr=atr,
            coinglass_data=cg_data,
            macro_data=macro,
            profile=profile,
            volatility_factor=volatility_factor
        )
        strategy = call_deepseek(prompt)

        if not strategy:
            raise Exception("DeepSeek 返回为空")

        # 如果方向非中性，用代码计算准确胜率
        if strategy.get("direction") != "neutral":
            strategy["win_rate"] = calculate_win_rate(strategy["direction"], cg_data, macro, profile)
        else:
            strategy["win_rate"] = 0

        # 计算信号强度
        signal_strength = calculate_signal_strength(strategy.get("direction", "neutral"), cg_data, macro)

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
            "signal_strength": signal_strength,  # 新增
        }

        markdown_msg = format_strategy_message(symbol, strategy, price, extra)
        success = send_dingtalk_message(markdown_msg, f"DeepSeek策略-{symbol}")
        if success:
            logger.info(f"{symbol} 策略推送成功")
        else:
            logger.error(f"{symbol} 推送失败")

    except Exception as e:
        logger.error(f"{symbol} 策略生成失败: {e}")
        logger.error(traceback.format_exc())
        send_error_notification(symbol, str(e))
        sys.exit(1)

    logger.info(f"===== {symbol} 流程结束 =====\n")

if __name__ == "__main__":
    main()
