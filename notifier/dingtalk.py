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
        hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        webhook = f"{webhook}&timestamp={timestamp}&sign={sign}"

    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": markdown_content}}
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
    now_str = now_beijing.strftime("%H:%M")
    direction = strategy.get("direction", "neutral")
    signal_quality = strategy.get("confidence", "medium").upper()
    is_probe = extra.get("is_probe", False)
    extreme_liq = extra.get("extreme_liq", False)

    if direction == "neutral":
        title = f"⏸️ [{symbol}] 中性观望 🕒 {now_str}"
    else:
        dir_text = "做多" if direction == "long" else "做空"
        probe_text = " 🧪" if is_probe else ""
        quality_star = {"HIGH": "★★★", "MEDIUM": "★★☆", "LOW": "★☆☆"}.get(signal_quality, "")
        title = f"{'🟢' if direction == 'long' else '🔴'} [{symbol}] {dir_text}{probe_text} {quality_star} 🕒 {now_str}"

    warning_line = "🚨 **极端清算警报**\n\n" if extreme_liq else ""

    if direction == "neutral":
        reasoning = strategy.get('reasoning', '当前多空力量均衡，无明显方向偏向。')
        # 提取第四步核心结论作为摘要，不截断原意
        summary = reasoning
        if "【第四步" in reasoning:
            parts = reasoning.split("【第四步")
            if len(parts) > 1:
                fourth = parts[1].split("【第五步")[0] if "【第五步" in parts[1] else parts[1]
                # 寻找“裁决”或“必须输出”所在句子，展示完整裁决理由
                if "裁决" in fourth:
                    lines = fourth.split("。")
                    for line in lines:
                        if "裁决" in line or "必须输出" in line or "neutral" in line.lower():
                            summary = line.strip() + "。"
                            break
                    else:
                        summary = fourth[:300] + "..." if len(fourth) > 300 else fourth
                else:
                    summary = fourth[:300] + "..." if len(fourth) > 300 else fourth
        else:
            summary = reasoning[:300] + "..." if len(reasoning) > 300 else reasoning
        summary = summary.replace("【", "").replace("】", "").strip()
        return f"""## {title}
{warning_line}当前价：${current_price:,.1f}

📊 {summary}"""

    entry_low = float(strategy.get("entry_price_low", current_price))
    entry_high = float(strategy.get("entry_price_high", current_price))
    stop = float(strategy.get("stop_loss", 0))
    tp1 = float(strategy.get("take_profit_1", 0))
    tp2 = float(strategy.get("take_profit_2", 0))

    reasoning = strategy.get('reasoning', '暂无分析')
    # 摘要：提取第四步核心裁决，保持完整语义，不截断关键逻辑
    summary = ""
    if "【第四步" in reasoning:
        parts = reasoning.split("【第四步")
        if len(parts) > 1:
            fourth = parts[1].split("【第五步")[0] if "【第五步" in parts[1] else parts[1]
            # 寻找包含“必须输出”、“裁决”或“强制”的完整句子
            sentences = fourth.replace("。", "。\n").split("\n")
            for s in sentences:
                if "必须输出" in s or "裁决" in s or "强制" in s or "输出" in s:
                    summary = s.strip() + "。"
                    break
            if not summary:
                # 取第四步前250字符，但尽量停在句号处
                if len(fourth) > 250:
                    last_period = fourth[:250].rfind("。")
                    summary = fourth[:last_period+1] if last_period != -1 else fourth[:250] + "..."
                else:
                    summary = fourth
    if not summary:
        # 回退：取reasoning前250字符
        if len(reasoning) > 250:
            last_period = reasoning[:250].rfind("。")
            summary = reasoning[:last_period+1] if last_period != -1 else reasoning[:250] + "..."
        else:
            summary = reasoning
    summary = summary.replace("【", "").replace("】", "").strip()

    # 风险提示：完整保留，一个字不截断
    risk_note = strategy.get('risk_note', '严格止损，TP1减仓50%，剩余移至成本价')

    quality_desc = {"HIGH": "高质量", "MEDIUM": "中等质量", "LOW": "低质量"}.get(signal_quality, "")

    return f"""## {title}
{warning_line}**入场**：${entry_low:,.1f} - ${entry_high:,.1f}
**止损**：${stop:,.1f}
**止盈1**：${tp1:,.1f} | **止盈2**：${tp2:,.1f}

📊 {summary}
📋 信号质量：{quality_desc}

⚠️ {risk_note}"""