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

    title_parts = [f"{dir_emoji} {dir_text} {symbol}"]
    if size_text:
        title_parts.append(size_text)
    title_parts.append(conf_text)
    title_parts.append(now_str)
    title = "## " + " · ".join(title_parts)

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

    reasoning = strategy.get("reasoning", "无推理过程")
    
    # ========== 格式化优化 ==========
    # 1. 步骤标题加粗
    reasoning = re.sub(r'(第[一二三四五六]步)[：:]', r'**\1：**', reasoning)
    
    # 2. “分析数据”和“做出结论”单独成行并加粗标记
    reasoning = re.sub(r'分析数据[：:]', r'\n> 📊 **分析数据**\n> ', reasoning)
    reasoning = re.sub(r'做出结论[：:]', r'\n> 📌 **做出结论**\n> ', reasoning)
    
    # 3. 第六步内部的关键节点单独成行并加粗
    reasoning = re.sub(r'交叉验证与裁决[：:]', r'\n> 🔍 **交叉验证与裁决**\n> ', reasoning)
    reasoning = re.sub(r'最终裁决[：:]', r'\n> ⚖️ **最终裁决**\n> ', reasoning)
    reasoning = re.sub(r'主逻辑[：:]', r'\n> 🧩 **主逻辑**\n> ', reasoning)
    reasoning = re.sub(r'核心逻辑[：:]', r'\n> 🧩 **核心逻辑**\n> ', reasoning)
    reasoning = re.sub(r'推演与决策[：:]', r'\n> 🎯 **推演与决策**\n> ', reasoning)
    reasoning = re.sub(r'价格(?:最可能)?路径[：:]', r'\n> 📈 **价格路径**\n> ', reasoning)
    
    # 4. 清理推演与决策中的列表项格式混乱
    # 4.1 先移除所有可能存在的孤立的 ">" 符号（在数字序号前）
    reasoning = re.sub(r'(\d+\.)\s*>\s*', r'\1 ', reasoning)
    # 4.2 将 "1. " 或 "1. " 格式的列表项统一转换为加粗格式，并确保换行
    reasoning = re.sub(r'(?<!\*\*)(\d+)\.[ \t]+', r'**\1.** ', reasoning)
    # 4.3 处理可能存在的 "1) " 或 "1、 " 格式
    reasoning = re.sub(r'(\d+)[\)、][ \t]+', r'**\1.** ', reasoning)
    
    # 5. 清理多余的连续换行和引用符号
    reasoning = re.sub(r'\n>\s*\n>', '\n> ', reasoning)
    reasoning = reasoning.strip()
    
    # 如果推理内容不是以引用格式开始，则添加引用格式
    if not reasoning.startswith('>'):
        lines = reasoning.split('\n')
        reasoning = '\n'.join([f'> {line}' if not line.startswith('>') and line.strip() else line for line in lines])

    risk_note = strategy.get("risk_note", "请严格设置止损")
    risk_items = re.split(r'[。；\n]', risk_note)
    risk_lines = []
    idx = 1
    for item in risk_items:
        item = item.strip()
        item = re.sub(r'^[\d]+[\.\、\)]?\s*', '', item)
        if item and len(item) > 2:
            risk_lines.append(f"{idx}. {item}")
            idx += 1
    if not risk_lines:
        risk_lines = ["1. 请严格设置止损"]
    risk_block = "> ### ⚠️ 风险说明\n> " + "\n> ".join(risk_lines)

    atr = data.get("atr", 0)
    funding = data.get("funding_rate", 0)
    oi_chg = data.get("oi_change_24h", 0)
    cvd_slope = data.get("cvd_slope", 0)
    cvd_dir = "↗" if cvd_slope > 0 else ("↘" if cvd_slope < 0 else "→")
    fg = data.get("fear_greed", 50)
    footnote = f"📎 ATR{atr:.0f} · 费率{funding:.4f}% · OI{oi_chg:+.1f}% · CVD{cvd_dir} · 贪婪{fg}"

    return f"""{title}

{param_card}

### 🧠 交易员推理
{reasoning}

{risk_block}

{footnote}
"""
