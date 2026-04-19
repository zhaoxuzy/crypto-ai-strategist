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
    now_str = now_beijing.strftime("%Y-%m-%d %H:%M")
    direction = strategy.get("direction", "neutral")
    data_source_status = extra.get("data_source_status", "")
    volatility_factor = extra.get("volatility_factor", 1.0)
    extreme_liq = extra.get("extreme_liq", False)

    # 市场状态
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

    if direction == "long":
        dir_display = "做多"
    elif direction == "short":
        dir_display = "做空"
    else:
        dir_display = "观望"

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

    # 原始 reasoning
    reasoning = strategy.get('reasoning', '暂无分析')

    # 1. 移除第五步及之后的内容
    if "【第五步" in reasoning:
        reasoning = reasoning.split("【第五步")[0].strip()

    # 2. 提取各步骤内容，重构为标准格式
    step_pattern = r'【(第[一二三四]步)[：:]?(.*?)】(.*?)(?=【第[一二三四]步|$)'
    steps = {}
    for match in re.finditer(step_pattern, reasoning, re.DOTALL):
        step_num = match.group(1)
        step_title = match.group(2).strip()
        step_content = match.group(3).strip()
        steps[step_num] = {"title": step_title, "content": step_content}

    # 如果解析失败，回退到简单的分割方法
    if not steps:
        parts = reasoning.split('【')
        for part in parts:
            if not part.strip():
                continue
            if '第一步' in part:
                steps['第一步'] = {"title": "清算动力学定锚", "content": part.split('】', 1)[-1].strip() if '】' in part else part.strip()}
            elif '第二步' in part:
                steps['第二步'] = {"title": "多空博弈找“犯错方”", "content": part.split('】', 1)[-1].strip() if '】' in part else part.strip()}
            elif '第三步' in part:
                steps['第三步'] = {"title": "宏观过滤器定基调", "content": part.split('】', 1)[-1].strip() if '】' in part else part.strip()}
            elif '第四步' in part:
                steps['第四步'] = {"title": "信号共振与矛盾裁决", "content": part.split('】', 1)[-1].strip() if '】' in part else part.strip()}

    # 3. 提取每步的结论，并构建标准标题
    formatted_steps = []
    step_names = {
        '第一步': '清算动力学定锚',
        '第二步': '多空博弈找“犯错方”',
        '第三步': '宏观过滤器定基调',
        '第四步': '信号共振与矛盾裁决'
    }

    # 第一步结论
    step1 = steps.get('第一步', {})
    content1 = step1.get('content', '')
    conclusion1 = ""
    if '偏多' in content1 and '偏空' not in content1:
        conclusion1 = "偏多"
    elif '偏空' in content1:
        conclusion1 = "偏空"
    elif '风险预警' in content1:
        conclusion1 = "风险预警"
    elif '中性观察' in content1:
        conclusion1 = "中性观察"
    else:
        # 从末尾提取
        lines = content1.split('\n')
        for line in reversed(lines):
            if '【' in line and '】' in line:
                conclusion1 = line.strip('【】')
                break
    formatted_steps.append(f"【第一步：{step_names['第一步']}，结论：{conclusion1}】\n{content1}")

    # 第二步结论
    step2 = steps.get('第二步', {})
    content2 = step2.get('content', '')
    conclusion2 = ""
    if '【偏多】' in content2:
        conclusion2 = "偏多"
    elif '【偏空】' in content2:
        conclusion2 = "偏空"
    elif '【中性偏空】' in content2:
        conclusion2 = "中性偏空"
    elif '【中性偏多】' in content2:
        conclusion2 = "中性偏多"
    elif '【中性】' in content2:
        conclusion2 = "中性"
    else:
        lines = content2.split('\n')
        for line in reversed(lines):
            if '【' in line and '】' in line:
                conclusion2 = line.strip('【】')
                break
    formatted_steps.append(f"【第二步：{step_names['第二步']}，结论：{conclusion2}】\n{content2}")

    # 第三步结论
    step3 = steps.get('第三步', {})
    content3 = step3.get('content', '')
    conclusion3 = ""
    if '【支持多头】' in content3:
        conclusion3 = "支持做多"
    elif '【支持空头】' in content3:
        conclusion3 = "支持做空"
    elif '【中性】' in content3:
        conclusion3 = "中性"
    else:
        # 尝试从权重分析中提取
        if "多头总权重 > 空头总权重" in content3:
            conclusion3 = "支持做多"
        elif "空头总权重 > 多头总权重" in content3:
            conclusion3 = "支持做空"
        else:
            conclusion3 = "中性"
    formatted_steps.append(f"【第三步：{step_names['第三步']}，结论：{conclusion3}】\n{content3}")

    # 第四步结论（最终裁决方向）
    step4 = steps.get('第四步', {})
    content4 = step4.get('content', '')
    conclusion4 = ""
    if direction == "long":
        conclusion4 = "做多"
    elif direction == "short":
        conclusion4 = "做空"
    else:
        conclusion4 = "观望"
    # 确保第四步内容末尾的裁决结论被移除（避免重复），然后整合到标题中
    content4 = re.sub(r'\n?【裁决结论】.*$', '', content4).strip()
    formatted_steps.append(f"【第四步：{step_names['第四步']}，结论：{conclusion4}】\n{content4}")

    reasoning = "\n\n".join(formatted_steps)

    # 方向倾向得分差值、多空明细
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

    score_detail = f"多头{bull_score}分 vs 空头{bear_score}分"

    if direction == "neutral":
        alerts_str = "\n".join(alerts) if alerts else ""
        return f"""## 🤖 DeepSeek 短线策略 [{symbol}] | 🕒 {now_str}
📈市场状态：{market_state} | 波动因子：{volatility_factor:.2f}
{alerts_str}
### 📊 AI 研判
{reasoning}
- 当前价：${current_price:,.1f}
- 资金费率：{extra.get('funding_rate', 'N/A')}%
- 分差：{diff}分（{strength_text}）| {score_detail}
- {data_source_status}
"""

    entry_low = float(strategy.get("entry_price_low", 0))
    entry_high = float(strategy.get("entry_price_high", 0))
    stop = float(strategy.get("stop_loss", 0))
    tp = float(strategy.get("take_profit", 0))

    # 计算盈亏比
    risk = abs(current_price - stop) if stop != 0 else 0
    reward = abs(tp - current_price) if tp != 0 else 0
    rr = reward / risk if risk > 0 else 0
    rr_str = f"盈亏比{rr:.2f}:1" if rr > 0 else "盈亏比N/A"

    alerts_str = "\n".join(alerts) if alerts else ""
    if alerts_str:
        alerts_str = alerts_str + "\n"

    # 处理风险提示：清洗并条目化
    risk_note = strategy.get('risk_note', '请严格设置止损')
    risk_note = re.sub(r'\s+', ' ', risk_note).strip()
    risk_note = re.sub(r'^\d+[\.、]\s*', '', risk_note)
    risk_items = [item.strip() for item in re.split(r'[。；;]', risk_note) if item.strip()]
    if not risk_items:
        risk_items = [risk_note]
    risk_items = [item for item in risk_items if not re.match(r'^\d+$', item)]
    risk_formatted = "\n".join([f"{i+1}. {item}" for i, item in enumerate(risk_items)])

    return f"""## 🤖 DeepSeek 短线策略 [{symbol}] | 🕒 {now_str}

### 📊 策略概要
- 市场状态：{market_state}
- 合约方向：{dir_display}
- 当前价格：${current_price:,.1f}
- 入场区间：${entry_low:,.1f} - ${entry_high:,.1f}
- 止损：${stop:,.1f}
- 止盈：${tp:.1f}（{rr_str}）
- 分差：{diff}分（{strength_text}）| {score_detail}

### 📈 AI 分析逻辑
{reasoning}

### ⚠️ 风险提示
{risk_formatted}

### 🔗 数据快照
- 当前价：${current_price:,.1f} | ATR：{extra.get('atr', 0):.1f}
- 资金费率：{extra.get('funding_rate', 'N/A')}% | OI 24h：{extra.get('oi_change', 'N/A')}% | 多空比：{extra.get('ls_ratio', 'N/A')}
- 恐惧贪婪：{extra.get('fear_greed', 'N/A')} | CVD：{extra.get('cvd_signal', 'N/A')}
- {data_source_status}
---
*本策略由DeepSeek基于实时市场数据生成，仅供参考。*
"""
