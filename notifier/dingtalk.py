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


def extract_core_reasoning(reasoning_raw: str) -> str:
    if not reasoning_raw:
        return ""
    text = reasoning_raw
    core = ""
    m = re.search(r'(交叉验证与裁决[：:][\s\S]*?)(?=入场区间|止损位|止盈位|主动证伪|微观盘口|如果我错了|方向选择|$)', text, re.DOTALL)
    if m:
        core = m.group(1).strip()
    if not core:
        core = text[:1500].strip()
        if len(text) > 1500:
            core += "..."
    if "如果我错了" not in core:
        wrong_m = re.search(r'(如果我错了[，,][\s\S]*?)(?=入场区间|止损位|止盈位|主动证伪|微观盘口|方向选择|$)', text, re.DOTALL)
        if wrong_m:
            core = core + "\n\n" + wrong_m.group(1).strip()
    if len(core) > 2000:
        core = core[:2000] + "..."
    return core


def extract_detail_steps(reasoning_raw: str) -> str:
    if not reasoning_raw:
        return ""
    m = re.search(r'(第一步[：:][\s\S]*?)(?=第六步[：:]|交叉验证与裁决|$)', reasoning_raw, re.DOTALL)
    if m:
        detail = m.group(1).strip()
        if len(detail) > 3000:
            detail = detail[:3000] + "..."
        return detail
    return ""


def force_line_breaks(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'(第[一二三四五六]步[：:])', r'\n\n\1', text)
    text = re.sub(r'(流动性猎杀推演|价格路径推演|情景推演)[：:]', r'\n\n\1：', text)
    text = re.sub(r'(分析数据[：:])', r'\n\1', text)
    text = re.sub(r'(第一反应[：:])', r'\n\1', text)
    text = re.sub(r'(自我质疑[：:])', r'\n\1', text)
    text = re.sub(r'(最终结论[：:])', r'\n\1', text)
    text = re.sub(r'(交叉验证与裁决[：:])', r'\n\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def format_reasoning_block(text: str) -> str:
    if not text:
        return "> "
    text = force_line_breaks(text)
    lines = text.split('\n')
    quoted = []
    for line in lines:
        line = line.strip()
        if not line:
            quoted.append('> ')
            continue
        if re.match(r'^(第[一二三四五六]步)', line):
            line = re.sub(r'^(第[一二三四五六]步)', r'**\1**', line)
        elif re.match(r'^(交叉验证与裁决|流动性猎杀推演|价格路径推演|如果我错了)', line):
            line = re.sub(r'^([^：:]+)', r'**\1**', line)
        quoted.append(f'> {line}' if not line.startswith('>') else line)
    return '\n'.join(quoted)


def clean_risk_text(raw: str) -> list:
    """彻底清洗风险文本，移除所有前导序号和标签"""
    lines = []
    for part in raw.split('\n'):
        part = part.strip()
        if not part:
            continue
        # 关键修复：先去除左侧空白，再反复剥离序号字符
        part = part.lstrip()
        while True:
            m = re.match(r'^([\d\.、\)）①②③④⑤⑥⑦⑧⑨⑩\s\t]+)(.*)$', part)
            if m:
                part = m.group(2).strip()
            else:
                break
        part = re.sub(r'^[-*•]\s*', '', part)
        part = re.sub(r'^(主要)?风险[：:]\s*', '', part)
        part = part.strip()
        if part and part not in lines:
            lines.append(part)
    return lines if lines else ["请严格设置止损"]


def format_strategy_message(symbol: str, strategy: dict, data: dict) -> str:
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).strftime("%m-%d %H:%M")

    direction = strategy.get("direction", "neutral")
    if direction == "neutral":
        title = f"## ⚪ 观望 {symbol} · 🔴低 · {now}"
        param = f"> 现价{data.get('mark_price', 0):.0f} · 入场0-0 · 止损0 · 止盈0 · 盈亏比N/A"
        core_block = "> 当前无交易机会，观望。"
        detail_block = ""
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

        reasoning_raw = strategy.get("reasoning", "")
        core_text = extract_core_reasoning(reasoning_raw)
        core_block = format_reasoning_block(core_text)

        detail_text = extract_detail_steps(reasoning_raw)
        if detail_text:
            detail_block = "\n\n---\n\n### 📋 完整推演过程\n" + format_reasoning_block(detail_text)
        else:
            detail_block = ""

    risk_lines = clean_risk_text(strategy.get("risk_note", "请严格设置止损"))
    risk_items = '\n> '.join([f"{i+1}. {s}" for i, s in enumerate(risk_lines)])
    risk_block = f"> ### ⚠️ 风险说明\n> {risk_items}"

    atr = data.get("atr_15m", 0)
    funding = data.get("funding_rate", 0)
    oi_chg = data.get("oi_change_24h", 0)
    cvd = data.get("cvd_slope", 0)
    cvd_dir = "↗" if cvd > 0 else ("↘" if cvd < 0 else "→")
    fg = data.get("fear_greed", 50)
    foot = f"📎 ATR{atr:.0f} · 费率{funding:.4f}% · OI{oi_chg:+.1f}% · CVD{cvd_dir} · 贪婪{fg}"

    message = f"{title}\n\n{param}\n\n### 🧠 核心逻辑\n{core_block}\n\n{risk_block}"
    if detail_block:
        message += detail_block
    message += f"\n\n{foot}"
    return message