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


def format_strategy_message(symbol: str, strategy: dict, data: dict) -> str:
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).strftime("%m-%d %H:%M")

    direction = strategy.get("direction", "neutral")
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
        current = data.get("mark_price", 0)

        mid = (entry_low + entry_high) / 2 if entry_low and entry_high else 0
        risk = abs(mid - stop) if stop else 0
        reward = abs(tp - mid) if tp else 0
        rr = reward / risk if risk > 0 else 0
        rr_str = f"{rr:.2f}" if rr else "N/A"

        param = f"> 现价{current:.0f} · 入场{entry_low:.0f}-{entry_high:.0f} · 止损{stop:.0f} · 止盈{tp:.0f} · 盈亏比{rr_str}"

    # 推理内容：每行加 `> ` 引用，保留原始换行，并对步骤和关键标题加粗
    reasoning_raw = strategy.get("reasoning", "无推理过程")
    lines = reasoning_raw.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    quoted = []
    for line in lines:
        line = line.strip()
        if not line:
            quoted.append('> ')
        else:
            # 步骤标题加粗
            if re.match(r'^(第[一二三四五六]步)[：:]', line):
                line = re.sub(r'^(第[一二三四五六]步)', r'**\1**', line)
            # 价格推演、入场区间等标题加粗
            elif re.match(r'^(价格路径推演|入场区间|止损位|止盈位|主动证伪信号|微观盘口确认)[：:]', line):
                line = re.sub(r'^([^：:]+)', r'**\1**', line)
            quoted.append(f'> {line}' if not line.startswith('>') else line)
    reasoning_block = '\n'.join(quoted)

    # 风险说明：彻底清理内部序号，仅保留外层统一编号
    risk_raw = strategy.get("risk_note", "请严格设置止损")
    risk_lines = []
    for part in risk_raw.split('\n'):
        part = part.strip()
        if not part:
            continue
        part = re.sub(r'^[\d\.、\)）①②③④⑤⑥⑦⑧⑨⑩]+\s*', '', part)
        part = re.sub(r'^主要风险[：:]\s*', '', part)
        part = part.strip()
        if part and part not in risk_lines:
            risk_lines.append(part)

    if not risk_lines:
        risk_lines = ["请严格设置止损"]

    risk_items = '\n> '.join([f"{i+1}. {s}" for i, s in enumerate(risk_lines)])
    risk_block = f"> ### ⚠️ 风险说明\n> {risk_items}"

    # 脚注
    atr = data.get("atr_15m", 0)
    funding = data.get("funding_rate", 0)
    oi_chg = data.get("oi_change_24h", 0)
    cvd = data.get("cvd_slope", 0)
    cvd_dir = "↗" if cvd > 0 else ("↘" if cvd < 0 else "→")
    fg = data.get("fear_greed", 50)
    foot = f"📎 ATR{atr:.0f} · 费率{funding:.4f}% · OI{oi_chg:+.1f}% · CVD{cvd_dir} · 贪婪{fg}"

    return f"{title}\n\n{param}\n\n### 🧠 交易员推理\n{reasoning_block}\n\n{risk_block}\n\n{foot}"
