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
    size_map = {"light": "轻", "medium": "中", "heavy": "重", "none": ""}
    size_text = size_map.get(position_size, "")
    
    confidence = strategy.get("confidence", "medium")
    conf_map = {"high": "🟢高", "medium": "🟡中", "low": "🔴低"}
    conf_text = conf_map.get(confidence, "🟡中")

    # 标题行
    title_parts = [f"{dir_emoji}{dir_text}{symbol}", now_str]
    if size_text:
        title_parts.append(f"{size_text}仓")
    title_parts.append(conf_text)
    title = "## " + " · ".join(title_parts)

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
    rr_str = f"{rr:.2f}" if rr > 0 else "N/A"
    
    param_card = f"> 现价{current_price:.0f} · 入场{entry_low:.0f}-{entry_high:.0f} · 止损{stop:.0f} · 止盈{tp:.0f} · 盈亏比{rr_str}"

    # 市场快照
    price_pct = data.get("price_percentile", 50)
    atr = data.get("atr", 0)
    vol = data.get("vol_factor", 1.0)
    above = data.get("above_liq", 0) / 1e9
    below = data.get("below_liq", 0) / 1e9
    ratio = data.get("liq_ratio", 0)
    imb = data.get("orderbook_imbalance", 0)
    netflow = data.get("netflow", 0) / 1e6
    fg = data.get("fear_greed", 50)
    fg_prev = data.get("fear_greed_prev_7d", 50)
    fg_trend = "↑" if fg > fg_prev else ("↓" if fg < fg_prev else "→")
    top_ls = data.get("top_ls_ratio", 0)
    oi_chg = data.get("oi_change_24h", 0)
    oi_pct = data.get("oi_percentile", 50)
    funding = data.get("funding_rate", 0)
    fund_pct = data.get("funding_percentile", 50)
    cvd_slope = data.get("cvd_slope", 0)
    cvd_dir = "↗" if cvd_slope > 0 else ("↘" if cvd_slope < 0 else "→")
    max_pain = data.get("max_pain", 0)

    snapshot_line1 = f"📊 价格{current_price:.0f}({price_pct:.0f}%) · ATR{atr:.0f} · 波动{vol:.2f} · 清算上{above:.2f}B/下{below:.2f}B(比值{ratio:.2f})"
    snapshot_line2 = f"📖 订单簿{imb:.3f} · 净流{netflow:.1f}M · 贪婪{fg}({fg_trend}) · 顶级{top_ls:.2f} · OI{oi_chg:+.1f}%({oi_pct:.0f}%) · 费率{funding:.4f}%({fund_pct:.0f}%) · CVD{cvd_dir}"

    # 数据缺失声明
    missing_items = []
    if max_pain == 0:
        missing_items.append("期权最大痛点")
    if netflow == 0:
        missing_items.append("期货资金净流")
    missing_text = ""
    if missing_items:
        missing_text = f"\n⚠️ 数据缺失：{', '.join(missing_items)}，相关分析置信度降低"

    # 六步推演（确保换行对齐）
    reasoning = strategy.get("reasoning", "无推理过程")
    if "【步骤" in reasoning and "\n" not in reasoning:
        parts = re.split(r'(【步骤\d+】)', reasoning)
        new_parts = []
        for i, p in enumerate(parts):
            if p.startswith("【步骤"):
                if i > 0:
                    new_parts.append("\n")
                new_parts.append(p)
            else:
                new_parts.append(p)
        reasoning = "".join(new_parts).strip()

    # 风险提示清理
    risk_note = strategy.get("risk_note", "请严格设置止损")
    risk_note = re.sub(r'^主要风险[：:]\s*', '', risk_note)
    risk_note = re.sub(r'反面情景预案[：:]', '', risk_note)
    raw_items = re.split(r'[。；\n]', risk_note)
    risk_lines = []
    idx = 1
    for item in raw_items:
        item = item.strip()
        if not item or len(item) < 3:
            continue
        item = re.sub(r'^\s*\d+[\.、\s]*[\)）]?\s*', '', item)
        if item and not re.match(r'^\d+$', item):
            risk_lines.append(f"{idx}. {item}")
            idx += 1
    if not risk_lines:
        risk_lines = ["1. 请严格设置止损"]
    risk_block = "> ### ⚠️ 风险\n> " + "\n> ".join(risk_lines)

    # 脚注
    footnote = f"📎 ATR{atr:.0f} · 费率{funding:.4f}% · OI{oi_chg:+.1f}% · CVD{cvd_dir} · 贪婪{fg}"

    return f"""{title}

{param_card}

{snapshot_line1}
{snapshot_line2}{missing_text}

### 🧠 六步推演
{reasoning}

{risk_block}

{footnote}
"""