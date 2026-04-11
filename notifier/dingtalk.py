import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import requests
from utils.logger import logger

def send_dingtalk_message(markdown_content: str, title: str = "策略推送"):
    webhook = os.getenv("DINGTALK_WEBHOOK_URL", "")
    secret = os.getenv("DINGTALK_SECRET", "")

    if not webhook:
        logger.error("未配置钉钉 Webhook")
        return False

    timestamp = str(round(time.time() * 1000))
    if secret and secret.lower() != "none":
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode(), string_to_sign.encode(), digestmod=hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        webhook = f"{webhook}&timestamp={timestamp}&sign={sign}"

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": markdown_content
        }
    }

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        result = resp.json()
        if result.get("errcode") == 0:
            logger.info("钉钉推送成功")
            return True
        else:
            logger.error(f"钉钉推送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"钉钉请求异常: {e}")
        return False

def format_single_strategy(symbol: str, strategy: dict, current_price: float, extra: dict) -> str:
    """格式化单个币种的策略（用于汇总）"""
    now_str = time.strftime("%Y-%m-%d %H:%M")
    direction = strategy.get("direction", "neutral")
    conf = strategy.get("confidence", "medium").upper()
    win_rate = strategy.get("win_rate", 50)

    if direction == "neutral":
        return f"""### ⏸️ {symbol}：中性观望
> {strategy.get('reasoning', '当前多空力量均衡')}
- 当前价：${current_price:,.1f} | 资金费率：{extra.get('funding_rate', 'N/A')}%
"""

    dir_text = "做多" if direction == "long" else "做空"
    entry_low = float(strategy.get("entry_price_low", 0))
    entry_high = float(strategy.get("entry_price_high", 0))
    stop = float(strategy.get("stop_loss", 0))
    tp1 = float(strategy.get("take_profit_1", 0))
    tp2 = float(strategy.get("take_profit_2", 0))
    position = float(strategy.get("position_size_ratio", 0.1))

    return f"""### 🤖 {symbol} {dir_text} | 置信度：{conf} | 预估胜率：{win_rate}%
- **入场**：${entry_low:,.1f} - ${entry_high:,.1f}
- **止损**：${stop:,.1f} | **止盈1**：${tp1:,.1f} | **止盈2**：${tp2:,.1f}
- **仓位**：{int(position*100)}%
- **分析**：{strategy.get('reasoning', '暂无')}
- **风险**：{strategy.get('risk_note', '请严格止损')}
"""

def send_multi_strategies(strategies_data: list, macro_data: dict):
    """
    发送多币种策略汇总
    strategies_data: 每个元素为 (symbol, strategy, price, extra)
    """
    now_str = time.strftime("%Y-%m-%d %H:%M")
    fg = macro_data.get("fear_greed", {})

    content = f"""## 📊 DeepSeek 多币种短线策略 🕒 {now_str}

**宏观背景**：恐惧贪婪指数 {fg.get('value', 'N/A')}（{fg.get('classification', '')}）

"""
    for symbol, strategy, price, extra in strategies_data:
        content += format_single_strategy(symbol, strategy, price, extra) + "\n"

    content += "\n---\n*本策略由DeepSeek基于实时数据生成，仅供参考。*"
    return send_dingtalk_message(content, "多币种策略推送")
