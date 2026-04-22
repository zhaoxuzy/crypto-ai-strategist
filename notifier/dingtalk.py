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


def format_reasoning(raw_text: str) -> str:
    """
    温和结构化处理：仅在关键位置插入换行，其余保留原样。
    """
    if not raw_text:
        return "无推理过程"

    # 1. 统一换行符，并压缩多余空行
    text = raw_text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 2. 在步骤标题前插入换行（确保每步独立成段）
    text = re.sub(r'(第[一二三四五六]步[：:])', r'\n\1', text)

    # 3. 在“分析数据”和“做出结论”前插入换行，使其独立成行（但后面不额外换行）
    text = re.sub(r'(分析数据[：:])', r'\n\1 ', text)   # 加空格避免与后面内容粘连
    text = re.sub(r'(做出结论[：:])', r'\n\1 ', text)

    # 4. 在“交叉验证与裁决”、“推演与决策”前插入换行
    text = re.sub(r'(交叉验证与裁决[：:])', r'\n\1 ', text)
    text = re.sub(r'(推演与决策[：:])', r'\n\1 ', text)

    # 5. 处理推演中的数字列表项（如 "1. 价格路径推演："），在前面换行
    text = re.sub(r'(\d+\.\s*价格路径推演[：:])', r'\n\1', text)
    text = re.sub(r'(\d+\.\s*入场区间)', r'\n\1', text)
    text = re.sub(r'(\d+\.\s*止损位)', r'\n\1', text)

    # 6. 移除可能产生的连续换行
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 7. 按行添加引用标记 "> "
    lines = text.split('\n')
    quoted = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            quoted.append('> ')   # 空行保留为引用块空行
            continue
        if stripped.startswith('>'):
            quoted.append(stripped)
        else:
            quoted.append(f'> {stripped}')

    # 8. 压缩连续的空引用行（最多保留一个）
    cleaned = []
    prev_empty = False
    for qline in quoted:
        is_empty = (qline.strip() == '>')
        if is_empty and prev_empty:
            continue
        cleaned.append(qline)
        prev_empty = is_empty

    return '\n'.join(cleaned)


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

    # 标题行
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

    # 推理内容格式化
    reasoning_raw = strategy.get("reasoning", "无推理过程")
    reasoning_block = format_reasoning(reasoning_raw)

    # 风险说明
    risk_note = strategy.get("risk_note", "请严格设置止损")
    risk_lines = []
    for part in risk_note.split('\n'):
        part = part.strip()
        if part:
            # 移除开头的编号前缀
            part = re.sub(r'^[\d\.、\)）]+\s*', '', part)
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
    cvd_slope = data.get("cvd_slope", 0)
    cvd_dir = "↗" if cvd_slope > 0 else ("↘" if cvd_slope < 0 else "→")
    fg = data.get("fear_greed", 50)
    footnote = f"📎 ATR{atr:.0f} · 费率{funding:.4f}% · OI{oi_chg:+.1f}% · CVD{cvd_dir} · 贪婪{fg}"

    # 拼接最终消息
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
    return '\n\n'.join(message_parts)