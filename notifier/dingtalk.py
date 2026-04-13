import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import requests
from datetime import datetime, timezone, timedelta
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

def format_strategy_message(symbol: str, strategy: dict, current_price: float, extra: dict) -> str:
    beijing_tz = timezone(timedelta(hours=8))
    now_beijing = datetime.now(beijing_tz)
    now_str = now_beijing.strftime("%Y-%m-%d %H:%M")

    direction = strategy.get("direction", "neutral")
    conf = strategy.get("confidence", "medium").upper()
    win_rate = strategy.get("win_rate", 50)

    if direction == "neutral":
        return f"""## ⏸️ [{symbol}] 短线策略：中性观望 🕒 {now_str}

### 📊 AI 研判
> {strategy.get('reasoning', '当前多空力量均衡，无明显方向偏向。')}

- 当前价：${current_price:,.1f}
- 资金费率：{extra.get('funding_rate', 'N/A')}%
"""

    dir_text = "做多" if direction == "long" else "做空"
    entry_low = float(strategy.get("entry_price_low", 0))
    entry_high = float(strategy.get("entry_price_high", 0))
    stop = float(strategy.get("stop_loss", 0))
    tp1 = float(strategy.get("take_profit_1", 0))
    tp2 = float(strategy.get("take_profit_2", 0))
    tp1_anchor = strategy.get("tp1_anchor", "未提供")
    tp2_anchor = strategy.get("tp2_anchor", "未提供")
    position = float(strategy.get("position_size_ratio", 0.1))

    return f"""## 🤖 DeepSeek 短线策略 [{symbol}] | 置信度：{conf} | 预估胜率：{win_rate}% 🕒 {now_str}

### 📊 策略概要
- **方向**：{dir_text}
- **当前价**：${current_price:,.1f}
- **入场区间**：${entry_low:,.1f} - ${entry_high:,.1f}
- **止损**：${stop:,.1f}
- **止盈1**：${tp1:,.1f}（锚定：{tp1_anchor}）
- **止盈2**：${tp2:,.1f}（锚定：{tp2_anchor}）
- **建议仓位**：{int(position*100)}%

### 📈 AI 分析逻辑
> {strategy.get('reasoning', '暂无分析')}

### ⚠️ 风险提示
- {strategy.get('risk_note', '请严格设置止损')}

### 🔗 数据快照
- 当前价：${current_price:,.1f} | ATR：{extra.get('atr', 0):.1f}
- 资金费率：{extra.get('funding_rate', 'N/A')}% | OI 24h：{extra.get('oi_change', 'N/A')}% | 多空比：{extra.get('ls_ratio', 'N/A')}
- 恐惧贪婪：{extra.get('fear_greed', 'N/A')} | CVD：{extra.get('cvd_signal', 'N/A')}

---
*本策略由DeepSeek基于实时市场数据生成，仅供参考。*
"""
