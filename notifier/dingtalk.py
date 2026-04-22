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
    
    # ========== 全新格式化策略：先结构化，再渲染 ==========
    # 1. 先为关键节点强制添加换行（确保正则能匹配）
    reasoning = re.sub(r'(第[一二三四五六]步)[：:]?\s*', r'\n【\1】\n', reasoning)
    reasoning = re.sub(r'分析数据[：:]', r'\n📊 **分析数据**\n', reasoning)
    reasoning = re.sub(r'做出结论[：:]', r'\n📌 **做出结论**\n', reasoning)
    reasoning = re.sub(r'交叉验证与裁决[：:]', r'\n🔍 **交叉验证与裁决**\n', reasoning)
    reasoning = re.sub(r'主逻辑[：:]', r'\n🧩 **主逻辑**\n', reasoning)
    reasoning = re.sub(r'推演与决策[：:]', r'\n🎯 **推演与决策**\n', reasoning)
    reasoning = re.sub(r'微观盘口确认[：:]', r'\n🔬 **微观盘口确认**\n', reasoning)
    
    # 2. 按行处理，重建结构
    lines = reasoning.split('\n')
    formatted_lines = []
    in_tuijue = False
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        # 检测推演区域
        if '🎯 **推演与决策**' in stripped or '推演与决策' in stripped:
            in_tuijue = True
            formatted_lines.append('> 🎯 **推演与决策**')
            continue
        
        # 检测微观盘口确认（可能单独出现）
        if '🔬 **微观盘口确认**' in stripped or '微观盘口确认' in stripped:
            in_tuijue = False
            formatted_lines.append('> 🔬 **微观盘口确认**')
            continue
        
        # 步骤标题特殊处理
        if stripped.startswith('【') and '】' in stripped:
            step_name = stripped.strip('【】')
            formatted_lines.append(f'> **{step_name}**')
            continue
        
        # 推演区域内处理列表项
        if in_tuijue:
            match = re.match(r'^(\d+)\.[ \t]*(.+)$', stripped)
            if match:
                num = match.group(1)
                content = match.group(2).strip()
                content = re.sub(r'\n', ' ', content)
                formatted_lines.append(f'> **{num}.** {content}')
                continue
        
        # 已格式化的标题行直接保留
        if stripped.startswith('📊') or stripped.startswith('📌') or stripped.startswith('🔍') or stripped.startswith('🧩'):
            formatted_lines.append(f'> {stripped}')
            continue
        
        # 默认：添加引用标记
        if not stripped.startswith('>'):
            formatted_lines.append(f'> {stripped}')
        else:
            formatted_lines.append(stripped)
    
    reasoning = '\n'.join(formatted_lines)
    reasoning = re.sub(r'\n>\s*\n>', '\n> ', reasoning)

    # ========== 风险说明彻底清洗 ==========
    risk_note = strategy.get("risk_note", "请严格设置止损")
    risk_note = re.sub(r'^\s*\d+[\.、\)）]\s*', '', risk_note, flags=re.MULTILINE)
    risk_note = re.sub(r'\n\s*\d+[\.、\)）]\s*', '\n', risk_note)
    raw_sentences = re.split(r'[。；\n]+', risk_note)
    clean_sentences = []
    for s in raw_sentences:
        s = s.strip()
        s = re.sub(r'^[\d\.、\)）]+\s*', '', s)
        s = re.sub(r'^(风险说明|风险提示|风险|主要风险)[：:]?\s*', '', s)
        if len(s) > 2:
            clean_sentences.append(s)
    unique_sentences = []
    for s in clean_sentences:
        if s not in unique_sentences:
            unique_sentences.append(s)
    if not unique_sentences:
        unique_sentences = ["请严格设置止损"]
    risk_lines = [f"{i+1}. {s}" for i, s in enumerate(unique_sentences)]
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