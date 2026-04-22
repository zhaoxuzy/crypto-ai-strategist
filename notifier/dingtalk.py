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
    size_map = {"light": "轻仓", "medium": "中仓", "heavy": "重仓", "none": ""}
    size_text = size_map.get(position_size, "")

    confidence = strategy.get("confidence", "medium")
    conf_map = {"high": "🟢高", "medium": "🟡中", "low": "🔴低"}
    conf_text = conf_map.get(confidence, "🟡中")

    # 标题
    title_parts = [f"{dir_emoji} {dir_text} {symbol}"]
    if size_text:
        title_parts.append(size_text)
    title_parts.append(conf_text)
    title_parts.append(now_str)
    title_line = "## " + " · ".join(title_parts)

    # 参数卡片
    entry_low = strategy.get("entry_price_low", 0)
    entry_high = strategy.get("entry_price_high", 0)
    stop = strategy.get("stop_loss", 0)
    tp = strategy.get("take_profit", 0)
    current_price = data.get("mark_price", 0)

    entry_mid = (entry_low + entry_high) / 2 if entry_low and entry_high else 0
    risk = abs(entry_mid - stop) if stop != 0 else 0
    reward = abs(tp - entry_mid) if tp != 0 else 0
    rr = reward / risk if risk > 0 else 0
    rr_str = f"{rr:.2f}" if rr > 0 else "N/A"

    param_card = f"> 现价{current_price:.0f} · 入场{entry_low:.0f}-{entry_high:.0f} · 止损{stop:.0f} · 止盈{tp:.0f} · 盈亏比{rr_str}"

    # ========== 推理内容精细化格式化 ==========
    reasoning_raw = strategy.get("reasoning", "无推理过程")

    # 1. 统一换行符，并压缩多余空行
    reasoning = reasoning_raw.replace('\r\n', '\n').replace('\r', '\n')
    reasoning = re.sub(r'\n{3,}', '\n\n', reasoning)

    # 2. 步骤标题加粗（第一步、第二步……）
    reasoning = re.sub(r'(第[一二三四五六]步)[：:]', r'**\1**：', reasoning)

    # 3. 强制在“分析数据：”和“做出结论：”前后换行，但不在它们后面额外加空行
    reasoning = re.sub(r'分析数据[：:]', r'\n分析数据：\n', reasoning)
    reasoning = re.sub(r'做出结论[：:]', r'\n做出结论：\n', reasoning)

    # 4. 处理第六步中的特殊子标题，确保它们单独成行且左对齐
    #    交叉验证与裁决、主逻辑、推演与决策、微观盘口确认
    reasoning = re.sub(r'(交叉验证与裁决)[：:]', r'\n\1：\n', reasoning)
    reasoning = re.sub(r'(主逻辑)[：:]', r'\n\1：\n', reasoning)
    reasoning = re.sub(r'(推演与决策)[：:]', r'\n\1：\n', reasoning)
    reasoning = re.sub(r'(微观盘口确认)[：:]', r'\n\1：\n', reasoning)

    # 5. 将文本按行拆分，每行添加引用标记 "> "
    lines = reasoning.split('\n')
    quoted_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            # 空行保留为单独的引用行（显示为空引用块），保持段落间距
            quoted_lines.append('> ')
            continue
        if stripped.startswith('>'):
            quoted_lines.append(stripped)
        else:
            quoted_lines.append(f'> {stripped}')

    # 6. 将连续的空引用行压缩为最多一个（避免过多空行）
    cleaned_quoted = []
    prev_empty = False
    for qline in quoted_lines:
        is_empty = (qline.strip() == '>')
        if is_empty and prev_empty:
            continue
        cleaned_quoted.append(qline)
        prev_empty = is_empty

    reasoning_block = '\n'.join(cleaned_quoted)

    # ========== 风险说明处理 ==========
    risk_note = strategy.get("risk_note", "请严格设置止损")
    risk_lines = []
    for part in risk_note.split('\n'):
        part = part.strip()
        if part:
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

    # ========== 最终消息拼接 ==========
    message_parts = [
        title_line,
        "",
        param_card,
        "",
        "### 🧠 交易员推理",
        reasoning_block,
        "",
        risk_block,
        "",
        footnote
    ]
    final_message = '\n\n'.join(message_parts)
    return final_message
