import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import requests
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

    # 市场状态：纯中文描述
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

    # 策略方向文本
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

    # 处理 AI 分析逻辑：每一步之间空一行
    reasoning = strategy.get('reasoning', '暂无分析')
    import re
    reasoning = re.sub(r'(【第[一二三四五]步)', r'\n\n\1', reasoning)
    reasoning = reasoning.strip()

    # ===== 计算信号强度星级 =====
    # 从 extra 中获取方向倾向得分差值，并计算星级
    directional_scores = extra.get("directional_scores", {})
    bull_score = directional_scores.get("bull", 0)
    bear_score = directional_scores.get("bear", 0)
    diff = abs(bull_score - bear_score)

    # 获取第一步结论（从 reasoning 中提取，若无则默认中性）
    first_step = ""
    if "【第一步" in reasoning:
        parts = reasoning.split("【第一步")
        if len(parts) > 1:
            first_step = parts[1].split("【第二步")[0] if "【第二步" in parts[1] else parts[1]
    first_step = first_step[:100] if first_step else ""

    # 根据绝对差值计算星级
    if diff >= 25:
        star = "★★★"
    elif diff >= 15:
        star = "★★☆"
    else:
        star = "★☆☆"

    if direction == "neutral":
        alerts_str = "\n".join(alerts) if alerts else ""
        return f"""## 🤖 DeepSeek 短线策略 [{symbol}] | 策略方向：{dir_display} 信号强度：{star} 🕒 {now_str}
📈市场状态：{market_state} | 波动因子：{volatility_factor:.2f}
{alerts_str}
### 📊 AI 研判
{reasoning}
- 当前价：${current_price:,.1f}
- 资金费率：{extra.get('funding_rate', 'N/A')}%
- 方向倾向差值：{diff}分
- {data_source_status}
"""

    entry_low = float(strategy.get("entry_price_low", 0))
    entry_high = float(strategy.get("entry_price_high", 0))
    stop = float(strategy.get("stop_loss", 0))
    tp1 = float(strategy.get("take_profit_1", 0))
    tp2 = float(strategy.get("take_profit_2", 0))
    tp1_anchor = strategy.get("tp1_anchor", "未提供")
    tp2_anchor = strategy.get("tp2_anchor", "未提供")

    alerts_str = "\n".join(alerts) if alerts else ""
    if alerts_str:
        alerts_str = alerts_str + "\n"

    return f"""## 🤖 DeepSeek 短线策略 [{symbol}] | 策略方向：{dir_display} 信号强度：{star} 🕒 {now_str}
📈市场状态：{market_state} | 波动因子：{volatility_factor:.2f}
{alerts_str}
### 📊 策略概要
- 方向：{dir_display}
- 当前价：${current_price:,.1f}
- 入场区间：${entry_low:,.1f} - ${entry_high:,.1f}
- 止损：${stop:,.1f}
- 止盈1：${tp1:,.1f}（锚定：{tp1_anchor}）
- 止盈2：${tp2:,.1f}（锚定：{tp2_anchor}）
- 方向倾向差值：{diff}分
- 止盈策略：TP1减仓50%，剩余仓位止损移至成本价，博取TP2。

### 📈 AI 分析逻辑
{reasoning}

### ⚠️ 风险提示
- {strategy.get('risk_note', '请严格设置止损')}

### 🔗 数据快照
- 当前价：${current_price:,.1f} | ATR：{extra.get('atr', 0):.1f}
- 资金费率：{extra.get('funding_rate', 'N/A')}% | OI 24h：{extra.get('oi_change', 'N/A')}% | 多空比：{extra.get('ls_ratio', 'N/A')}
- 恐惧贪婪：{extra.get('fear_greed', 'N/A')} | CVD：{extra.get('cvd_signal', 'N/A')}
- {data_source_status}
---
*本策略由DeepSeek基于实时市场数据生成，仅供参考。*
"""