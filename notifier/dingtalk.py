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


def clean_risk_text(raw: str) -> list:
    """清洗风险文本，返回干净的风险条目列表"""
    lines = []
    for part in raw.split('\n'):
        part = part.strip()
        if not part:
            continue
        # 移除所有常见序号前缀和标签
        part = re.sub(r'^[\d\.、\)）①②③④⑤⑥⑦⑧⑨⑩]+\s*', '', part)
        part = re.sub(r'^(主要)?风险[：:]\s*', '', part)
        part = part.strip()
        if part and part not in lines:
            lines.append(part)
    return lines if lines else ["请严格设置止损"]


def format_reasoning_text(text: str, bold_titles: bool = True) -> str:
    """将任意推理文本格式化为钉钉引用块，自动处理换行和标题加粗"""
    if not text:
        return "> "

    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # 强制在关键标签前换行，确保独立成行
    text = re.sub(r'(分析数据[：:])', r'\n\1 ', text)
    text = re.sub(r'(第一反应[：:])', r'\n\1 ', text)
    text = re.sub(r'(自我质疑[：:])', r'\n\1 ', text)
    text = re.sub(r'(最终结论[：:])', r'\n\1 ', text)
    text = re.sub(r'(交叉验证与裁决[：:])', r'\n\1 ', text)
    text = re.sub(r'(价格路径推演[：:])', r'\n\1 ', text)
    text = re.sub(r'(如果我错了[，,])', r'\n\1 ', text)
    text = re.sub(r'(第[一二三四五六]步[：:])', r'\n\n\1 ', text)

    lines = text.split('\n')
    quoted = []
    for line in lines:
        line = line.strip()
        if not line:
            quoted.append('> ')
            continue

        if bold_titles:
            # 步骤标题加粗
            if re.match(r'^第[一二三四五六]步', line):
                line = re.sub(r'^(第[一二三四五六]步)', r'**\1**', line)
            # 核心逻辑内部标题加粗
            elif re.match(r'^(交叉验证与裁决|价格路径推演|如果我错了)', line):
                line = re.sub(r'^([^：:]+)', r'**\1**', line)

        quoted.append(f'> {line}' if not line.startswith('>') else line)
    return '\n'.join(quoted)


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
        # 核心逻辑：截取包含“交叉验证”“价格推演”“如果我错了”的连续段落
        core_parts = []
        # 尝试从“交叉验证与裁决”开始，到“入场区间”或“主动证伪”之前结束
        match = re.search(r'(交叉验证与裁决[\s\S]+?)(?=入场区间|止损位|止盈位|主动证伪|微观盘口|$)', reasoning_raw, re.DOTALL)
        if match:
            core_text = match.group(1).strip()
        else:
            # 回退：取最后 1200 字符
            core_text = reasoning_raw[-1200:] if len(reasoning_raw) > 1200 else reasoning_raw

        core_block = format_reasoning_text(core_text, bold_titles=True)

        # 完整推演：第一步到第五步
        detail_match = re.search(r'(第一步[\s\S]+?)(?=第六步|交叉验证与裁决)', reasoning_raw, re.DOTALL)
        if detail_match:
            detail_text = detail_match.group(1).strip()
            detail_block = "\n\n---\n\n### 📋 完整推演过程\n" + format_reasoning_text(detail_text, bold_titles=True)
        else:
            detail_block = ""

    # 风险说明
    risk_lines = clean_risk_text(strategy.get("risk_note", "请严格设置止损"))
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

    message = f"{title}\n\n{param}\n\n### 🧠 核心逻辑\n{core_block}\n\n{risk_block}"
    if detail_block:
        message += detail_block
    message += f"\n\n{foot}"
    return message