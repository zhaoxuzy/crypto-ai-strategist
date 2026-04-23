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
    将AI推理文本转为钉钉引用块格式。
    严格遵循：不改变文本内容，仅添加 `> ` 前缀并对指定标题加粗。
    """
    if not text:
        return "> 无推理过程"

    # 统一换行符并压缩过多空行（保留原有段落结构）
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 按段落分割（以两个换行符为准）
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

            # 1. 步骤标题加粗：第一步：... 至 第六步：...
            if re.match(r'^第[一二三四五六]步[：:]', line):
                line = re.sub(r'^(第[一二三四五六]步)', r'**\1**', line)

            # 2. 分析数据、自我质疑、最终结论 首行展示且加粗
            elif re.match(r'^(分析数据|自我质疑|最终结论)[：:]', line):
                line = re.sub(r'^([^：:]+)', r'**\1**', line)

            # 3. 交叉验证与裁决、流动性猎杀推演 首行展示且加粗
            elif re.match(r'^(交叉验证与裁决|流动性猎杀推演)[：:]', line):
                line = re.sub(r'^([^：:]+)', r'**\1**', line)

            # 添加引用标记（避免重复添加）
            if line.startswith('>'):
                quoted_lines.append(line)
            else:
                quoted_lines.append(f'> {line}')

        formatted_paras.append('\n'.join(quoted_lines))

    # 段落之间用空行分隔（钉钉渲染出间距）
    return '\n\n'.join(formatted_paras)


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

        # 参数卡片
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

    # ----- 推理内容（使用格式化函数）-----
    reasoning_raw = strategy.get("reasoning", "无推理过程")
    reasoning_block = format_reasoning(reasoning_raw)

    # ----- 风险说明（完全保留AI输出的原始序号和内容，仅添加引用标记）-----
    risk_raw = strategy.get("risk_note", "请严格设置止损")
    risk_lines = []
    for part in risk_raw.split('\n'):
        part = part.strip()
        if not part:
            continue
        # 只添加引用标记，不改动任何内容（包括序号）
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

    # ----- 最终拼接 -----
    return f"{title}\n\n{param}\n\n### 🧠 交易员推理\n{reasoning_block}\n\n{risk_block}\n\n{foot}"
