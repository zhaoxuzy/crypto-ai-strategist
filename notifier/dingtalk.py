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
    size_map = {"light": "轻仓", "medium": "中仓", "heavy": "重仓", "none": "无仓位"}
    size_text = size_map.get(position_size, "")
    
    confidence = strategy.get("confidence", "medium")
    conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(confidence, "🟡")
    conf_text = {"high": "高置信度", "medium": "中置信度", "low": "低置信度"}.get(confidence, "中置信度")

    # 标题行
    title = f"## {dir_emoji} {dir_text} {symbol} | {now_str}"
    if position_size != "none":
        title += f" | {size_text}"
    title += f" | {conf_emoji} {conf_text}"

    # 交易指令卡
    entry_low = strategy.get("entry_price_low", 0)
    entry_high = strategy.get("entry_price_high", 0)
    stop = strategy.get("stop_loss", 0)
    tp = strategy.get("take_profit", 0)
    current_price = data.get("mark_price", 0)
    
    entry_mid = (entry_low + entry_high) / 2 if entry_low and entry_high else 0
    risk = abs(entry_mid - stop) if stop != 0 else 0
    reward = abs(tp - entry_mid) if tp != 0 else 0
    rr = reward / risk if risk > 0 else 0
    rr_str = f"{rr:.2f}:1" if rr > 0 else "N/A"
    
    param_card = f"> **现价** {current_price:.1f} · **入场** {entry_low:.0f}-{entry_high:.0f} · **止损** {stop:.0f} · **止盈** {tp:.0f} · **盈亏比** **{rr_str}**"

    # 市场快照
    price_percentile = data.get("price_percentile", 50)
    atr = data.get("atr", 0)
    vol_factor = data.get("vol_factor", 1.0)
    above_liq = data.get("above_liq", 0) / 1e9
    below_liq = data.get("below_liq", 0) / 1e9
    liq_ratio = data.get("liq_ratio", 0)
    orderbook_imbalance = data.get("orderbook_imbalance", 0)
    netflow = data.get("netflow", 0) / 1e6
    fear_greed = data.get("fear_greed", 50)
    fear_greed_prev = data.get("fear_greed_prev_7d", 50)
    fg_trend = "↑" if fear_greed > fear_greed_prev else ("↓" if fear_greed < fear_greed_prev else "持平")
    top_ls = data.get("top_ls_ratio", 0)
    oi_change = data.get("oi_change_24h", 0)
    oi_percentile = data.get("oi_percentile", 50)
    funding_rate = data.get("funding_rate", 0)
    funding_percentile = data.get("funding_percentile", 50)
    cvd_slope = data.get("cvd_slope", 0)
    cvd_dir = "正向" if cvd_slope > 0 else ("负向" if cvd_slope < 0 else "持平")
    
    snapshot = f"""### 📊 市场快照
📈 价格 {current_price:.1f} ({price_percentile:.0f}%分位) · 📊 ATR {atr:.1f} · 🌊 波动 {vol_factor:.2f}
🔥 上方清算 {above_liq:.2f}B · 💧 下方清算 {below_liq:.2f}B · ⚖️ 比值 {liq_ratio:.3f}
📖 订单簿 {orderbook_imbalance:.3f} · 💵 资金净流 {netflow:.1f}M
😨 贪婪 {fear_greed} ({fg_trend}) · 🐳 顶级多空 {top_ls:.2f} · 💰 OI {oi_change:+.1f}% ({oi_percentile:.0f}%分位)
💸 资金费率 {funding_rate:.4f}% ({funding_percentile:.0f}%分位) · 📉 CVD {cvd_dir}"""

    # AI 六步推演
    reasoning = strategy.get("reasoning", "无推理过程")
    
    # 风险警示
    risk_note = strategy.get("risk_note", "无")
    # 按句号或换行分割，生成编号列表
    risk_items = re.split(r'[。；\n]', risk_note)
    risk_lines = []
    index = 1
    for item in risk_items:
        item = item.strip()
        if item and not item.isspace():
            risk_lines.append(f"{index}. {item}")
            index += 1
    if not risk_lines:
        risk_lines = ["1. 请严格设置止损"]
    risk_formatted = "\n> ".join(risk_lines)
    risk_block = f"> ### ⚠️ 风险警示\n> {risk_formatted}"

    # 脚注
    footnote = f"📎 ATR {atr:.1f} · 费率 {funding_rate:.4f}% · OI {oi_change:+.1f}% · CVD {cvd_dir} · 贪婪 {fear_greed}"

    return f"""{title}

{param_card}

{snapshot}

### 🧠 AI 六步推演
{reasoning}

{risk_block}

{footnote}
"""
