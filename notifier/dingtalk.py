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


def send_dingtalk_message(markdown_content: str, title: str = "策略推送") -> bool:
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


def format_reasoning(raw_text: str) -> str:
    """将 AI 推理文本转换为钉钉引用块格式，保留原始结构并适度美化"""
    if not raw_text:
        return "> 无推理过程"

    # 统一换行符
    text = raw_text.replace('\r\n', '\n').replace('\r', '\n')
    # 将连续多个换行压缩为最多两个（段落分隔）
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 对关键标题进行加粗（钉钉支持 **加粗**）
    text = re.sub(r'(第[一二三四五六]步)[：:]', r'**\1**：', text)
    text = re.sub(r'(价格路径推演)[：:]', r'**\1**：', text)
    text = re.sub(r'(入场区间|止损位|止盈位|主动证伪信号|微观盘口确认)[：:]', r'**\1**：', text)

    # 按段落分割（以两个换行为准）
    paragraphs = text.split('\n\n')
    formatted_paras = []

    for para in paragraphs:
        if not para.strip():
            continue
        lines = para.split('\n')
        quoted_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 避免重复引用标记
            if line.startswith('>'):
                quoted_lines.append(line)
            else:
                quoted_lines.append(f'> {line}')
        # 段落内用单个换行连接
        formatted_paras.append('\n'.join(quoted_lines))

    # 段落之间用空行（即两个换行）分隔，钉钉会渲染出段落间距
    return '\n\n'.join(formatted_paras)


def format_strategy_message(symbol: str, strategy: dict, data: dict) -> str:
    beijing_tz = timezone(timedelta(hours=8))
    now_str = datetime.now(beijing_tz).strftime("%m-%d %H:%M")

    direction = strategy.get("direction", "neutral")

    # ========== 标题行 ==========
    if direction == "neutral":
        title_line = f"## ⚪ 观望 {symbol} · 🔴低 · {now_str}"
    else:
        dir_emoji = "🟢" if direction == "long" else "🔴"
        dir_text = "做多" if direction == "long" else "做空"

        position_size = strategy.get("position_size", "none")
        size_map = {"light": "轻仓", "medium": "中仓", "heavy": "重仓", "none": ""}
        size_text = size_map.get(position_size, "")

        confidence = strategy.get("confidence", "medium")
        conf_map = {"high": "🟢高", "medium": "🟡中", "low": "🔴低"}
        conf_text = conf_map.get(confidence, "🟡中")

        title_parts = [f"{dir_emoji} {dir_text} {symbol}"]
        if size_text:
            title_parts.append(size_text)
        title_parts.append(conf_text)
        title_parts.append(now_str)
        title_line = "## " + " · ".join(title_parts)

    # ========== 参数卡片 ==========
    entry_low = strategy.get("entry_price_low", 0)
    entry_high = strategy.get("entry_price_high", 0)
    stop = strategy.get("stop_loss", 0)
    tp = strategy.get("take_profit", 0)
    current_price = data.get("mark_price", 0)

    if direction == "neutral":
        param_card = f"> 现价{current_price:.0f} · 入场0-0 · 止损0 · 止盈0 · 盈亏比N/A"
    else:
        # 计算盈亏比用于展示
        entry_mid = (entry_low + entry_high) / 2 if entry_low and entry_high else 0
        risk = abs(entry_mid - stop) if stop != 0 else 0
        reward = abs(tp - entry_mid) if tp != 0 else 0
        rr = reward / risk if risk > 0 else 0
        rr_str = f"{rr:.2f}" if rr else "N/A"
        param_card = f"> 现价{current_price:.0f} · 入场{entry_low:.0f}-{entry_high:.0f} · 止损{stop:.0f} · 止盈{tp:.0f} · 盈亏比{rr_str}"

    # ========== 推理内容 ==========
    reasoning_raw = strategy.get("reasoning", "无推理过程")
    reasoning_block = format_reasoning(reasoning_raw)

    # ========== 风险说明 ==========
    risk_note = strategy.get("risk_note", "请严格设置止损")
    risk_lines = []
    for part in risk_note.split('\n'):
        part = part.strip()
        if part:
            # 移除可能的前缀编号
            part = re.sub(r'^[\d\.、\)）]+\s*', '', part)
            if part and part not in risk_lines:
                risk_lines.append(part)
    if not risk_lines:
        risk_lines = ["请严格设置止损"]

    risk_items = '\n> '.join([f"{i+1}. {s}" for i, s in enumerate(risk_lines)])
    risk_block = f"> ### ⚠️ 风险说明\n> {risk_items}"

    # ========== 脚注 ==========
    atr = data.get("atr_15m", 0)
    funding = data.get("funding_rate", 0)
    oi_chg = data.get("oi_change_24h", 0)
    cvd_slope = data.get("cvd_slope", 0)
    cvd_dir = "↗" if cvd_slope > 0 else ("↘" if cvd_slope < 0 else "→")
    fg = data.get("fear_greed", 50)
    footnote = f"📎 ATR{atr:.0f} · 费率{funding:.4f}% · OI{oi_chg:+.1f}% · CVD{cvd_dir} · 贪婪{fg}"

    # ========== 拼接最终消息 ==========
    # 使用明确的空行分隔各模块
    message = (
        f"{title_line}\n\n"
        f"{param_card}\n\n"
        f"### 🧠 交易员推理\n"
        f"{reasoning_block}\n\n"
        f"{risk_block}\n\n"
        f"{footnote}"
    )
    return message
