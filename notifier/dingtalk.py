import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import requests
import re
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
        hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        webhook = f"{webhook}&timestamp={timestamp}&sign={sign}"
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": markdown_content}}
    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.json().get("errcode") == 0:
            logger.info("钉钉推送成功")
            return True
        else:
            logger.error(f"钉钉推送失败: {resp.json()}")
            return False
    except Exception as e:
        logger.error(f"钉钉请求异常: {e}")
        return False


def format_strategy_message(symbol: str, strategy: dict, data: dict) -> str:
    beijing_tz = timezone(timedelta(hours=8))
    now_str = datetime.now(beijing_tz).strftime("%m-%d %H:%M")
    direction = strategy.get("direction", "neutral")
    dir_emoji = "🟢" if direction == "long" else ("🔴" if direction == "short" else "⚪")
    dir_text = "做多" if direction == "long" else ("做空" if direction == "short" else "观望")
    position_size = strategy.get("position_size", "none")
    size_map = {"light": "轻仓", "medium": "中仓", "heavy": "重仓", "none": "无仓位"}
    size_text = size_map.get(position_size, "")

    title = f"## {dir_emoji} {dir_text} {symbol} | {now_str}"
    if position_size != "none":
        title += f" | {size_text}"

    entry_low = strategy.get("entry_price_low", 0)
    entry_high = strategy.get("entry_price_high", 0)
    stop = strategy.get("stop_loss", 0)
    tp = strategy.get("take_profit", 0)
    current_price = data.get("mark_price", 0)

    entry_mid = (entry_low + entry_high) / 2 if entry_low and entry_high else 0
    risk = abs(entry_mid - stop) if stop != 0 else 0
    reward = abs(tp - entry_mid) if tp != 0 else 0
    rr = reward / risk if risk > 0 else 0
    rr_str = f"{rr:.2f}:1" if rr > 0 else "N/A"

    reasoning = strategy.get("reasoning", "无推理过程")
    risk_note = strategy.get("risk_note", "无")

    # 数据快照行
    atr = data.get("atr", 0)
    funding = data.get("funding_rate", 0)
    oi_change = data.get("oi_change_24h", 0)
    cvd_slope = data.get("cvd_slope", 0)
    cvd_dir = "正向" if cvd_slope > 0 else ("负向" if cvd_slope < 0 else "持平")
    fear_greed = data.get("fear_greed", 50)
    netflow = data.get("netflow", 0) / 1e6
    netflow_dir = "流入" if netflow > 0 else "流出"

    snapshot = f"📎 `ATR {atr:.1f}` · `费率 {funding:.4f}%` · `OI {oi_change:+.1f}%` · `CVD {cvd_dir}` · `贪婪 {fear_greed}` · `资金{netflow_dir} {abs(netflow):.1f}M`"

    return f"""{title}

> **现价**：{current_price:.1f} | **入场**：{entry_low:.1f}-{entry_high:.1f} | **止损**：{stop:.1f} | **止盈**：{tp:.1f} | **盈亏比**：{rr_str}

### 🧠 AI 六步推演
{reasoning}

> ### ⚠️ 风险警示
> {risk_note}

{snapshot}
"""
