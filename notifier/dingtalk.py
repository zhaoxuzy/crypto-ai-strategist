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


def _extract_section(text: str, step_pattern: str, labels: list) -> dict:
    """
    从文本中提取指定步骤的内容，并尝试拆分为各个子标签。
    返回一个字典，键为子标签名，值为内容。
    """
    section_match = re.search(step_pattern, text, re.DOTALL)
    if not section_match:
        return {label: "【未提供】" for label in labels}

    section_text = section_match.group(1).strip()

    result = {}
    for label in labels:
        # 匹配标签及后续内容，直到遇到下一个标签或段落结束
        pattern = rf'{label}[：:]\s*(.*?)(?=\n(?:{"|".join(labels)})[：:]|\Z)'
        match = re.search(pattern, section_text, re.DOTALL)
        if match:
            content = match.group(1).strip()
            # 清理多余换行和空格
            content = re.sub(r'\s+', ' ', content)
            result[label] = content
        else:
            result[label] = "【未提供】"

    return result


def _extract_liquidity_hunt(text: str) -> str:
    """提取流动性猎杀推演内容"""
    patterns = [
        r'【流动性猎杀推演[】]?\s*(.*?)(?=入场区间|止损位|止盈位|主动证伪|微观盘口|$)',
        r'流动性猎杀推演[：:]\s*(.*?)(?=入场区间|止损位|止盈位|主动证伪|微观盘口|$)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            content = match.group(1).strip()
            content = re.sub(r'\s+', ' ', content)
            return content
    return "【未提供】"


def format_reasoning(text: str) -> str:
    """
    智能解析AI推理文本，按标准六步框架重构，确保格式绝对受控。
    """
    if not text:
        return "> 无推理过程"

    # 定义六步的提取模式
    steps = [
        {"name": "第一步：环境定调", "pattern": r"第一步[：:]\s*环境定调\s*(.*?)(?=第二步[：:]|$)", "labels": ["分析数据", "第一反应", "自我质疑", "最终结论"]},
        {"name": "第二步：猎物定位", "pattern": r"第二步[：:]\s*猎物定位\s*(.*?)(?=第三步[：:]|$)", "labels": ["分析数据", "第一反应", "自我质疑", "最终结论"]},
        {"name": "第三步：对手盘解剖", "pattern": r"第三步[：:]\s*对手盘解剖\s*(.*?)(?=第四步[：:]|$)", "labels": ["分析数据", "第一反应", "自我质疑", "最终结论"]},
        {"name": "第四步：资金流验证", "pattern": r"第四步[：:]\s*资金流验证\s*(.*?)(?=第五步[：:]|$)", "labels": ["分析数据", "第一反应", "自我质疑", "最终结论"]},
        {"name": "第五步：辅助信号", "pattern": r"第五步[：:]\s*辅助信号\s*(.*?)(?=第六步[：:]|交叉验证|$)", "labels": ["分析数据", "第一反应", "自我质疑", "最终结论"]},
        {"name": "第六步：矛盾裁决与决策", "pattern": r"第六步[：:]\s*矛盾裁决与决策\s*(.*?)(?=【流动性猎杀推演|入场区间|$)", "labels": ["交叉验证与裁决"]},
    ]

    # 提取第六步后的入场等信息
    extra_labels = ["入场区间", "止损位", "止盈位", "主动证伪信号", "微观盘口确认"]
    extra_data = {}
    for label in extra_labels:
        match = re.search(rf'{label}[：:]\s*(.*?)(?=\n(?:{"|".join(extra_labels)})[：:]|\Z)', text, re.DOTALL)
        if match:
            extra_data[label] = re.sub(r'\s+', ' ', match.group(1).strip())
        else:
            extra_data[label] = "【未提供】"

    # 提取流动性猎杀推演
    liquidity = _extract_liquidity_hunt(text)

    # 构建标准输出
    output_lines = []
    for step in steps:
        output_lines.append(f"> **{step['name']}**")
        section_data = _extract_section(text, step["pattern"], step["labels"])
        for label in step["labels"]:
            content = section_data.get(label, "【未提供】")
            output_lines.append(f">   **{label}**：")
            output_lines.append(f">     {content}")
        output_lines.append("> ")  # 空行分隔

    # 添加流动性猎杀推演
    output_lines.append(f"> **流动性猎杀推演**：")
    output_lines.append(f">   {liquidity}")
    output_lines.append("> ")

    # 添加入场、止损、止盈等信息
    for label in extra_labels:
        content = extra_data[label]
        output_lines.append(f"> **{label}**：")
        output_lines.append(f">   {content}")

    return '\n'.join(output_lines)


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

    # ----- 推理内容（智能重构）-----
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