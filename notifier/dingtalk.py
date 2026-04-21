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

    # 彻底清洗数据源状态，防止加粗加大（零宽空格方案）
    data_source_status = extra.get("data_source_status", "")
    data_source_status = re.sub(r'[*_`#>\-]', '', data_source_status)
    data_source_status = data_source_status.replace('：', ':\u200b')
    data_source_status = re.sub(r'\s+', ' ', data_source_status).strip()
    if not data_source_status:
        data_source_status = "清算数据源:\u200b model2(主用)"

    volatility_factor = extra.get("volatility_factor", 1.0)
    extreme_liq = extra.get("extreme_liq", False)

    trend_info = extra.get("trend_info", {})
    trend_direction = trend_info.get("direction", "neutral")
    trend_score = trend_info.get("score", 0)

    if trend_direction == "bull":
        if trend_score >= 70: market_state = "上涨趋势"
        elif trend_score >= 30: market_state = "震荡偏强"
        else: market_state = "弱势震荡"
    elif trend_direction == "bear":
        if trend_score >= 70: market_state = "下跌趋势"
        elif trend_score >= 30: market_state = "震荡偏弱"
        else: market_state = "弱势震荡"
    else:
        market_state = "无明显方向"

    if 30 <= trend_score <= 70:
        market_state += "（方向不明）"

    dir_emoji = "🟢" if direction == "long" else ("🔴" if direction == "short" else "⚪")
    dir_text = "做多" if direction == "long" else ("做空" if direction == "short" else "观望")

    alerts = []
    funding_rate_str = extra.get("funding_rate", "0")
    try:
        fr = float(funding_rate_str.strip('%')) if isinstance(funding_rate_str, str) else 0
        if fr > 0.05: alerts.append("⚠️资金费率>0.05%(多头拥挤)")
        elif fr < -0.03: alerts.append("⚠️资金费率<-0.03%(空头拥挤)")
    except: pass

    oi_change_str = extra.get("oi_change", "0")
    try:
        oi = float(oi_change_str.strip('%')) if isinstance(oi_change_str, str) else 0
        if abs(oi) > 5: alerts.append(f"⚠️OI24h变化{oi:.1f}%(大幅{'增' if oi>0 else '减'}仓)")
    except: pass

    if extreme_liq:
        alerts.append("🚨极端清算警报")

    # 获取新版字段，若不存在则回退到旧版 analysis_summary
    panorama = strategy.get('panorama', '')
    verdict = strategy.get('verdict', '')

    if not panorama and not verdict:
        # 回退：使用旧的 analysis_summary
        analysis_summary = strategy.get('analysis_summary', '')
        if not analysis_summary:
            reasoning = strategy.get('reasoning', '暂无分析')
            if "【第五步" in reasoning:
                reasoning = reasoning.split("【第五步")[0].strip()
            analysis_summary = reasoning[:500] + "..." if len(reasoning) > 500 else reasoning
        panorama = analysis_summary
        verdict = ""

    # 格式化全景扫描：按 🔍 分割，内部换行用 <br>
    formatted_panorama = ""
    if panorama:
        parts = re.split(r'(?=🔍)', panorama)
        summary_items = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            part = part.replace('\n', '<br>')
            summary_items.append(part)
        if summary_items:
            formatted_panorama = "\n".join([f"- {item}" for item in summary_items])
        else:
            formatted_panorama = panorama
    else:
        formatted_panorama = "无全景扫描"

    # 格式化深度研判：内部换行用 <br>
    formatted_verdict = verdict.replace('\n', '<br>') if verdict else ""

    # 提取最终裁决（如果 verdict 中包含，可单独展示）
    final_verdict = ""
    if "最终裁决:" in verdict or "5.最终裁决:" in verdict:
        # 从 verdict 中提取最终裁决行
        match = re.search(r'(?:5\.)?最终裁决[:：]\s*(.+?)(?=\n|$)', verdict, re.DOTALL)
        if match:
            final_verdict = match.group(1).strip()

    trader_commentary = strategy.get('trader_commentary', '')

    directional_scores = extra.get("directional_scores", {})
    bull_score = directional_scores.get("bull", 0)
    bear_score = directional_scores.get("bear", 0)
    diff = abs(bull_score - bear_score)

    if diff >= 22: strength_text = "强"
    elif diff >= 12: strength_text = "中"
    elif diff >= 8: strength_text = "弱"
    else: strength_text = "极弱"

    title_line = f"## {dir_emoji} {dir_text} {symbol}  |  {now_beijing.strftime('%m-%d %H:%M')}"

    if direction == "neutral":
        alerts_str = "\n".join(alerts) if alerts else ""
        final_block = f"\n> **📌 最终裁决**：{final_verdict}" if final_verdict else ""
        verdict_block = f"\n> **📌 深度研判**：{formatted_verdict}" if formatted_verdict else ""
        return f"""{title_line}

📈 市场状态：{market_state} | 波动因子 {volatility_factor:.2f}
{alerts_str}

### 🧠 AI 研判摘要
{formatted_panorama}
{verdict_block}
{final_block}

- 当前价：${current_price:,.1f}
- 资金费率：{extra.get('funding_rate', 'N/A')}%
- 分差：{diff}分（{strength_text}）| 多头{bull_score} vs 空头{bear_score}
- {data_source_status}
"""

    entry_low = float(strategy.get("entry_price_low", 0))
    entry_high = float(strategy.get("entry_price_high", 0))
    stop = float(strategy.get("stop_loss", 0))
    tp = float(strategy.get("take_profit", 0))

    # 基于入场中间价计算盈亏比
    entry_mid = (entry_low + entry_high) / 2
    risk = abs(entry_mid - stop) if stop != 0 else 0
    reward = abs(tp - entry_mid) if tp != 0 else 0
    rr = reward / risk if risk > 0 else 0
    rr_str = f"{rr:.2f}:1" if rr > 0 else "N/A"

    bar_len = int(min(100, trend_score) / 10)
    trend_bar = "`" + "█" * bar_len + "░" * (10 - bar_len) + "`"
    trend_state_desc = f"{trend_bar} {trend_score}/100"

    param_card = f"""
> ### 📋 交易指令
> **现价**：`{current_price:.1f}`  
> **入场**：`{entry_low:.1f}` — `{entry_high:.1f}`  
> **止损**：`{stop:.1f}` 🔴  
> **止盈**：`{tp:.1f}` 🟢  
> **盈亏比**：**{rr_str}**
"""

    # 风险提示清洗
    risk_note = strategy.get('risk_note', '请严格设置止损')
    risk_note = re.sub(r'^(风险提示|风险|主要风险)[：:]\s*', '', risk_note)
    risk_note = re.sub(r'\s+', ' ', risk_note).strip()
    raw_items = re.split(r'[。；;]', risk_note)
    risk_items = []
    for item in raw_items:
        item = item.strip()
        if not item: continue
        item = re.sub(r'^\s*\d+[\.、\s]*[\)）]?\s*', '', item)
        item = re.sub(r'^(风险提示|风险|主要风险)[：:]\s*', '', item)
        if item and not re.match(r'^\d+$', item):
            risk_items.append(item)
    if not risk_items:
        risk_items = ["请严格设置止损"]
    risk_formatted = "\n> ".join([f"{i+1}. {item.strip()}" for i, item in enumerate(risk_items)])

    alerts_str = "  ".join(alerts) if alerts else ""

    trader_block = ""
    if trader_commentary:
        trader_block = f"\n> 💬 **交易员备注**：{trader_commentary}\n"

    final_block = f"\n> **📌 最终裁决**：{final_verdict}" if final_verdict else ""
    verdict_block = f"\n> **📌 深度研判**：{formatted_verdict}" if formatted_verdict else ""

    # 数据快照行
    atr_val = extra.get('atr', 0)
    funding_val = extra.get('funding_rate', 'N/A')
    oi_val = extra.get('oi_change', 'N/A')
    cvd_val = extra.get('cvd_signal', 'N/A')
    greed_val = extra.get('fear_greed', 'N/A')

    if isinstance(oi_val, str) and oi_val != 'N/A' and not oi_val.endswith('%'):
        oi_val += '%'
    if isinstance(funding_val, str) and funding_val != 'N/A' and not funding_val.endswith('%'):
        funding_val += '%'

    snapshot_line = f"📎 `ATR {atr_val:.1f}` · `费率 {funding_val}` · `OI {oi_val}` · `CVD {cvd_val}` · `贪婪 {greed_val}`"

    return f"""{title_line}

{param_card}

### 📊 市场状态
趋势强度 {trend_state_desc} ({market_state})  
⚖️ 多空得分 `🟢 {bull_score}` vs `🔴 {bear_score}` (分差 {diff}，{strength_text}确信)  
{alerts_str}

### 🧠 AI 研判摘要
{formatted_panorama}
{verdict_block}
{final_block}
{trader_block}
### ⚠️ 风险警示
> {risk_formatted}

{snapshot_line}  
{data_source_status}
---
*以上内容由 DeepSeek 生成，仅供参考*
"""
