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
        result = resp.json()
        if result.get("errcode") == 0:
            logger.info("钉钉推送成功")
            return True
        else:
            logger.error(f"钉钉推送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"钉钉请求异常: {e}")
        return False


def format_strategy_message(symbol: str, strategy: dict, current_price: float, extra: dict) -> str:
    beijing_tz = timezone(timedelta(hours=8))
    now_beijing = datetime.now(beijing_tz)
    direction = strategy.get("direction", "neutral")
    data_source_status = extra.get("data_source_status", "")
    volatility_factor = extra.get("volatility_factor", 1.0)
    extreme_liq = extra.get("extreme_liq", False)

    # 市场状态文本（用于趋势进度条旁）
    trend_info = extra.get("trend_info", {})
    trend_direction = trend_info.get("direction", "neutral")
    trend_score = trend_info.get("score", 0)

    if trend_direction == "bull":
        if trend_score >= 70:
            market_state = "上涨趋势"
        elif trend_score >= 30:
            market_state = "震荡偏强"
        else:
            market_state = "弱势震荡"
    elif trend_direction == "bear":
        if trend_score >= 70:
            market_state = "下跌趋势"
        elif trend_score >= 30:
            market_state = "震荡偏弱"
        else:
            market_state = "弱势震荡"
    else:
        market_state = "无明显方向"

    if 30 <= trend_score <= 70:
        market_state += "（方向不明）"

    # 方向展示
    dir_emoji = "🟢" if direction == "long" else ("🔴" if direction == "short" else "⚪")
    dir_text = "做多" if direction == "long" else ("做空" if direction == "short" else "观望")

    # 预警信息收集
    alerts = []
    funding_rate_str = extra.get("funding_rate", "0")
    try:
        fr = float(funding_rate_str.strip('%')) if isinstance(funding_rate_str, str) else 0
        if fr > 0.05:
            alerts.append("⚠️资金费率>0.05%(多头拥挤)")
        elif fr < -0.03:
            alerts.append("⚠️资金费率<-0.03%(空头拥挤)")
    except:
        pass

    oi_change_str = extra.get("oi_change", "0")
    try:
        oi = float(oi_change_str.strip('%')) if isinstance(oi_change_str, str) else 0
        if abs(oi) > 5:
            alerts.append(f"⚠️OI24h变化{oi:.1f}%(大幅{'增' if oi>0 else '减'}仓)")
    except:
        pass

    if extreme_liq:
        alerts.append("🚨极端清算警报")

    # 提取 AI 四步结论摘要
    reasoning_raw = strategy.get('reasoning', '')
    step_conclusions = _extract_step_conclusions(reasoning_raw, direction)

    # 方向倾向得分差值
    directional_scores = extra.get("directional_scores", {})
    bull_score = directional_scores.get("bull", 0)
    bear_score = directional_scores.get("bear", 0)
    diff = abs(bull_score - bear_score)

    if diff >= 22:
        strength_text = "强"
    elif diff >= 12:
        strength_text = "中"
    elif diff >= 8:
        strength_text = "弱"
    else:
        strength_text = "极弱"

    # 标题行构建（含止盈预览）
    tp = float(strategy.get("take_profit", 0))
    title_line = f"## {dir_emoji} {dir_text} {symbol}  |  🕒 {now_beijing.strftime('%m-%d %H:%M')}"
    if direction != "neutral" and tp > 0:
        title_line += f"  |  🎯 止盈 {tp:.1f}"

    # 对于 neutral 方向，使用简化模板
    if direction == "neutral":
        alerts_str = "\n".join(alerts) if alerts else ""
        ai_summary_lines = []
        for step_name, conclusion in step_conclusions.items():
            ai_summary_lines.append(f"- **{step_name}**：{conclusion}")
        ai_summary = "\n".join(ai_summary_lines) if ai_summary_lines else "AI 未提供分析"

        return f"""{title_line}

📈 市场状态：{market_state} | 波动因子 {volatility_factor:.2f}
{alerts_str}

### 🧠 AI 研判摘要
{ai_summary}

- 当前价：${current_price:,.1f}
- 资金费率：{extra.get('funding_rate', 'N/A')}%
- 分差：{diff}分（{strength_text}）| 多头{bull_score} vs 空头{bear_score}
- {data_source_status}
"""

    # 非 neutral 方向的完整模板
    entry_low = float(strategy.get("entry_price_low", 0))
    entry_high = float(strategy.get("entry_price_high", 0))
    stop = float(strategy.get("stop_loss", 0))

    risk = abs(current_price - stop) if stop != 0 else 0
    reward = abs(tp - current_price) if tp != 0 else 0
    rr = reward / risk if risk > 0 else 0
    rr_str = f"{rr:.2f}:1" if rr > 0 else "N/A"

    # 趋势进度条（文本模拟）
    bar_len = int(min(100, trend_score) / 10)
    trend_bar = "`" + "█" * bar_len + "░" * (10 - bar_len) + "`"
    trend_state_desc = f"{trend_bar} {trend_score}/100"

    # 核心参数卡（引用块）
    param_card = f"""
> ### 📋 交易指令
> **入场**：`{entry_low:.1f}` — `{entry_high:.1f}`  
> **止损**：`{stop:.1f}` 🔴  
> **止盈**：`{tp:.1f}` 🟢  
> **盈亏比**：**{rr_str}**
"""

    # AI 分析摘要（四步结论列表）
    ai_summary_lines = []
    for step_name, conclusion in step_conclusions.items():
        ai_summary_lines.append(f"- **{step_name}**：{conclusion}")
    ai_summary = "\n".join(ai_summary_lines)

    # 风险提示清洗并编号
    risk_note = strategy.get('risk_note', '请严格设置止损')
    risk_note = re.sub(r'^风险提示[：:]\s*', '', risk_note)
    risk_note = re.sub(r'\s+', ' ', risk_note).strip()
    risk_note = re.sub(r'^\s*\d+[\.、\s]*[\)）]?\s*', '', risk_note)
    raw_items = re.split(r'[。；;]', risk_note)
    risk_items = []
    for item in raw_items:
        item = item.strip()
        if not item:
            continue
        item = re.sub(r'^\s*\d+[\.、\s]*[\)）]?\s*', '', item)
        if item and not re.match(r'^\d+$', item):
            risk_items.append(item)
    if not risk_items:
        risk_items = ["请严格设置止损"]
    # 每条风险单独一行，用引用块包裹
    risk_formatted = "\n> ".join([f"{i+1}. {item}" for i, item in enumerate(risk_items)])

    # 预警行
    alerts_str = "  ".join(alerts) if alerts else ""

    return f"""{title_line}

{param_card}

### 📊 市场状态
趋势强度 {trend_state_desc} ({market_state})  
⚖️ 多空得分 `🟢 {bull_score}` vs `🔴 {bear_score}` (分差 {diff}，{strength_text}确信)  
{alerts_str}

### 🧠 AI 研判摘要
{ai_summary}

### ⚠️ 风险警示
> {risk_formatted}

📎 `ATR {extra.get('atr',0):.1f}` · `费率 {extra.get('funding_rate','N/A')}%` · `OI {extra.get('oi_change','N/A')}%` · `CVD {extra.get('cvd_signal','N/A')}` · `贪婪 {extra.get('fear_greed','N/A')}`  
{data_source_status}
---
*以上内容由 DeepSeek 生成，仅供参考*
"""


def _extract_step_conclusions(reasoning: str, final_direction: str) -> dict:
    """
    从 reasoning 文本中提取四步法的结论，返回字典：
    {
        '第一步(清算)': '偏多',
        '第二步(犯错方)': '偏多',
        '第三步(宏观)': '支持做多',
        '第四步(裁决)': '做多'
    }
    """
    conclusions = {
        '第一步(清算)': '未知',
        '第二步(犯错方)': '未知',
        '第三步(宏观)': '未知',
        '第四步(裁决)': '观望'
    }

    # 第一步结论提取
    if '偏多' in reasoning and '偏空' not in reasoning:
        # 需要更精准定位第一步区域
        step1_pattern = r'【第[一二三四]步[：:]*清算[^】]*】(.*?)(?=【第[一二三四]步|$)'
        match = re.search(step1_pattern, reasoning, re.DOTALL)
        text = match.group(1) if match else reasoning
        if '偏多' in text:
            conclusions['第一步(清算)'] = '偏多'
        elif '偏空' in text:
            conclusions['第一步(清算)'] = '偏空'
        elif '风险预警' in text:
            conclusions['第一步(清算)'] = '风险预警'
        elif '中性观察' in text:
            conclusions['第一步(清算)'] = '中性观察'
        else:
            lines = text.split('\n')
            for line in reversed(lines):
                if '【' in line and '】' in line:
                    conclusions['第一步(清算)'] = line.strip('【】')
                    break

    # 第二步结论提取
    if '【偏多】' in reasoning:
        conclusions['第二步(犯错方)'] = '偏多'
    elif '【偏空】' in reasoning:
        conclusions['第二步(犯错方)'] = '偏空'
    elif '【中性偏空】' in reasoning:
        conclusions['第二步(犯错方)'] = '中性偏空'
    elif '【中性偏多】' in reasoning:
        conclusions['第二步(犯错方)'] = '中性偏多'
    elif '【中性】' in reasoning:
        conclusions['第二步(犯错方)'] = '中性'

    # 第三步结论提取
    if '【支持多头】' in reasoning:
        conclusions['第三步(宏观)'] = '支持做多'
    elif '【支持空头】' in reasoning:
        conclusions['第三步(宏观)'] = '支持做空'
    elif '【中性】' in reasoning:
        conclusions['第三步(宏观)'] = '中性'
    else:
        if "多头总权重 > 空头总权重" in reasoning:
            conclusions['第三步(宏观)'] = '支持做多'
        elif "空头总权重 > 多头总权重" in reasoning:
            conclusions['第三步(宏观)'] = '支持做空'

    # 第四步结论直接使用最终方向
    if final_direction == "long":
        conclusions['第四步(裁决)'] = '做多'
    elif final_direction == "short":
        conclusions['第四步(裁决)'] = '做空'
    else:
        conclusions['第四步(裁决)'] = '观望'

    return conclusions
