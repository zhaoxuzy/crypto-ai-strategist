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


def send_dingtalk_message(content: str, title: str = "策略推送") -> bool:
    webhook = os.getenv("DINGTALK_WEBHOOK_URL", "")
    secret = os.getenv("DINGTALK_SECRET", "")
    if not webhook:
        logger.error("未配置钉钉 Webhook")
        return False
    ts = str(round(time.time() * 1000))
    if secret and secret.lower() != "none":
        sign_str = f"{ts}\n{secret}"
        sign = urllib.parse.quote_plus(base64.b64encode(hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).digest()))
        webhook = f"{webhook}&timestamp={ts}&sign={sign}"
    try:
        resp = requests.post(webhook, json={"msgtype": "markdown", "markdown": {"title": title, "text": content}}, timeout=10)
        if resp.json().get("errcode") == 0:
            logger.info("钉钉推送成功")
            return True
        logger.error(f"钉钉失败: {resp.json()}")
        return False
    except Exception as e:
        logger.error(f"钉钉异常: {e}")
        return False


def format_reasoning(text: str) -> str:
    """
    极简稳定版：只做必要的美化，绝不篡改AI输出的文字内容。
    """
    if not text:
        return "> 无推理过程"

    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 1. 强制在关键主标题前插入换行符，确保分段清晰
    main_titles = [
        "第[一二三四五六]步[：:]",
        "流动性猎杀推演[：:]",
        "入场区间[：:]",
        "止损位[：:]",
        "止盈位[：:]",
        "主动证伪信号[：:]",
        "微观盘口确认[：:]"
    ]
    for title in main_titles:
        text = re.sub(rf'(?<!\n)({title})', r'\n\1', text)

    # 2. 按行处理，添加引用标记和加粗
    lines = text.split('\n')
    quoted_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            quoted_lines.append('> ')
            continue

        # 对主标题进行加粗
        if re.match(r'^(第[一二三四五六]步)[：:]', line):
            line = re.sub(r'^(第[一二三四五六]步)', r'**\1**', line)
        elif re.match(r'^(流动性猎杀推演|入场区间|止损位|止盈位|主动证伪信号|微观盘口确认)[：:]', line):
            line = re.sub(r'^([^：:]+)', r'**\1**', line)

        if line.startswith('>'):
            quoted_lines.append(line)
        else:
            quoted_lines.append(f'> {line}')

    # 压缩连续空行
    cleaned = []
    prev_empty = False
    for qline in quoted_lines:
        is_empty = (qline.strip() == '>')
        if is_empty and prev_empty:
            continue
        cleaned.append(qline)
        prev_empty = is_empty

    return '\n'.join(cleaned)


def format_strategy_message(symbol: str, strategy: dict, data: dict) -> str:
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).strftime("%m-%d %H:%M")

    direction = strategy.get("direction", "neutral")

    # ----- 标题行 -----
    if direction == "neutral":
        title = f"## ⚪ 观望 {symbol} · 🔴低 · {now}"
        param = f"> 现价{data.get('mark_price', 0):.0f} · 入场0-0 · 止损0 · 止盈0 · 盈亏比N/A"
    else:
        emoji = "🟢" if direction == "long" else "🔴"
        text = "做多" if direction == "long" else "做空"
        size = strategy.get("position_size", "none")
        size_cn = {"light": "轻仓", "medium": "中仓", "heavy": "重仓"}.get(size, "")
        conf = strategy.get("confidence", "medium")
        conf_cn = {"high": "🟢高", "medium": "🟡中", "low": "🔴低"}.get(conf, "🟡中")

        parts = [f"{emoji} {text} {symbol}"]
        if size_cn:
            parts.append(size_cn)
        parts.append(conf_cn)
        parts.append(now)
        title = "## " + " · ".join(parts)

        entry_low = strategy.get("entry_price_low", 0)
        entry_high = strategy.get("entry_price_high", 0)
        stop = strategy.get("stop_loss", 0)
        tp = strategy.get("take_profit", 0)
        current_price = data.get("mark_price", 0)

        mid = (entry_low + entry_high) / 2 if entry_low and entry_high else 0
        risk = abs(mid - stop) if stop else 0
        reward = abs(tp - mid) if tp else 0
        rr = reward / risk if risk > 0 else 0
        rr_str = f"{rr:.2f}" if rr else "N/A"

        param = f"> 现价{current_price:.0f} · 入场{entry_low:.0f}-{entry_high:.0f} · 止损{stop:.0f} · 止盈{tp:.0f} · 盈亏比{rr_str}"

    # ----- 推理内容（极简美化）-----
    reasoning_raw = strategy.get("reasoning", "无推理过程")
    reasoning_block = format_reasoning(reasoning_raw)

    # ----- 风险说明 -----
    risk_raw = strategy.get("risk_note", "请严格设置止损")
    risk_lines = []
    for part in risk_raw.split('\n'):
        part = part.strip()
        if not part:
            continue
        if part.startswith('>'):
            risk_lines.append(part)
        else:
            risk_lines.append(f'> {part}')

    if not risk_lines:
        risk_lines = ["> 请严格设置止损"]

    risk_block = "> ### ⚠️ 风险说明\n" + "\n".join(risk_lines)

    # ----- 脚注 -----
    atr = data.get("atr_15m", 0)
    funding = data.get("funding_rate", 0)
    oi_chg = data.get("oi_change_24h", 0)
    cvd = data.get("cvd_slope", 0)
    cvd_dir = "↗" if cvd > 0 else ("↘" if cvd < 0 else "→")
    fg = data.get("fear_greed", 50)
    foot = f"📎 ATR{atr:.0f} · 费率{funding:.4f}% · OI{oi_chg:+.1f}% · CVD{cvd_dir} · 贪婪{fg}"

    return f"{title}\n\n{param}\n\n### 🧠 交易员推理\n{reasoning_block}\n\n{risk_block}\n\n{foot}"