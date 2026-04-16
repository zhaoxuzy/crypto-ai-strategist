import os
import json
from openai import OpenAI
from utils.logger import logger

# ---------- 连续型评分辅助函数 ----------
def linear_score(value: float, low: float, high: float, full_score: float, reverse: bool = False) -> float:
    if low == high:
        return 0.0
    if reverse:
        if value <= low:
            return full_score
        if value >= high:
            return 0.0
        return full_score * (high - value) / (high - low)
    else:
        if value <= low:
            return 0.0
        if value >= high:
            return full_score
        return full_score * (value - low) / (high - low)


def get_position_structure_score(direction: str, coinglass_data: dict, macro_data: dict) -> tuple:
    score = 0.0
    details = []
    top_ls = coinglass_data.get("top_long_short_ratio", "N/A")
    try:
        tls = float(top_ls)
        if direction == "long":
            if tls <= 0.7:
                s = 20.0
            elif tls <= 2.0:
                s = linear_score(tls, 0.7, 2.0, 20, reverse=True)
            else:
                s = 0.0
        else:
            if tls >= 2.0:
                s = 20.0
            elif tls >= 0.7:
                s = linear_score(tls, 0.7, 2.0, 20, reverse=False)
            else:
                s = 0.0
        score += s
        if s > 1:
            details.append(f"顶级持仓结构({tls:.2f})")
        elif tls != "N/A" and ((direction == "long" and tls > 2.0) or (direction == "short" and tls < 0.7)):
            score -= 20 * 0.4
            details.append(f"顶级持仓反向({tls:.2f})")
    except:
        pass
    ls_account = coinglass_data.get("ls_account_ratio", 1.0)
    try:
        lsa = float(ls_account)
        if direction == "long":
            if lsa <= 0.7:
                s = 12.0
            elif lsa <= 2.0:
                s = linear_score(lsa, 0.7, 2.0, 12, reverse=True)
            else:
                s = 0.0
        else:
            if lsa >= 2.0:
                s = 12.0
            elif lsa >= 0.7:
                s = linear_score(lsa, 0.7, 2.0, 12, reverse=False)
            else:
                s = 0.0
        score += s
        if s > 1:
            details.append(f"人数比({lsa:.2f})")
        elif lsa != 1.0 and ((direction == "long" and lsa > 2.0) or (direction == "short" and lsa < 0.7)):
            score -= 12 * 0.3
            details.append(f"人数比反向({lsa:.2f})")
    except:
        pass
    return score, details


LIQ_MIN_THRESHOLDS = {
    "BTC": 50_000_000,
    "ETH": 20_000_000,
    "SOL": 5_000_000
}


def calculate_signal_strength(symbol: str, direction: str, coinglass_data: dict, macro_data: dict, liq_zero_count: int = 0, eth_btc_data: dict = None, balance_data: dict = None) -> dict:
    total_score = 0.0
    signals_detail = []
    min_liq_threshold = LIQ_MIN_THRESHOLDS.get(symbol.upper(), 50_000_000)

    # ---- 1. 清算方向（25分）----
    above = coinglass_data.get("above_short_liquidation", "0")
    below = coinglass_data.get("below_long_liquidation", "0")
    try:
        above_val = float(above.replace(",", "")) if isinstance(above, str) else float(above)
        below_val = float(below.replace(",", "")) if isinstance(below, str) else float(below)
        total_liq = above_val + below_val
        scale_factor = min(1.0, total_liq / min_liq_threshold) if total_liq > 0 else 0.0
        if total_liq > 0:
            short_ratio = above_val / total_liq
            if direction == "short":
                raw_s = linear_score(short_ratio, 0.5, 0.8, 25, reverse=False)
            else:
                raw_s = linear_score(short_ratio, 0.2, 0.5, 25, reverse=True)
            s = raw_s * scale_factor
            total_score += s
            if s > 5:
                if scale_factor < 0.5:
                    signals_detail.append(f"清算结构({short_ratio:.1%}, 规模小)")
                else:
                    signals_detail.append(f"清算结构({short_ratio:.1%})")
            if direction == "long" and below_val == 0:
                total_score -= 15
                signals_detail.append("下方无多头清算(风险极大)")
            if (direction == "long" and short_ratio > 0.6) or (direction == "short" and short_ratio < 0.4):
                total_score -= 25 * 0.4
                signals_detail.append("清算结构反向")
    except:
        pass

    # ---- 2. 持仓结构因子（29分）----
    pos_score, pos_details = get_position_structure_score(direction, coinglass_data, macro_data)
    total_score += pos_score * (29/32)
    signals_detail.extend(pos_details)

    # ---- 3. CVD（11分）----
    cvd = coinglass_data.get("cvd_signal", "N/A")
    if cvd in ["bullish", "slightly_bullish"]:
        if direction == "long":
            s = 11.0 if cvd == "bullish" else 7.0
            total_score += s
            signals_detail.append(f"CVD:{cvd}")
        else:
            total_score -= 11 * 0.5
            signals_detail.append("CVD反向")
    elif cvd in ["bearish", "slightly_bearish"]:
        if direction == "short":
            s = 11.0 if cvd == "bearish" else 7.0
            total_score += s
            signals_detail.append(f"CVD:{cvd}")
        else:
            total_score -= 11 * 0.5
            signals_detail.append("CVD反向")

    # ---- 4. 恐惧贪婪（7分）----
    fg = macro_data.get("fear_greed", {})
    fg_val = int(fg.get("value", 50))
    if direction == "long":
        s = linear_score(fg_val, 20, 50, 7, reverse=True)
    else:
        s = linear_score(fg_val, 50, 80, 7, reverse=False)
    total_score += s
    if s > 2:
        signals_detail.append(f"恐惧贪婪({fg_val})")
    if (direction == "long" and fg_val > 70) or (direction == "short" and fg_val < 30):
        total_score -= 7 * 0.4
        signals_detail.append("情绪反向")

    # ---- 5. 资金费率（4分）----
    funding_rate = coinglass_data.get("funding_rate", "N/A")
    try:
        fr = float(funding_rate)
        if direction == "short":
            s = linear_score(fr, 0.02, 0.08, 4, reverse=False)
        else:
            s = linear_score(fr, -0.08, -0.01, 4, reverse=True)
        total_score += s
        if abs(s) > 1:
            signals_detail.append(f"资金费率({fr:.4f})")
        if (direction == "long" and fr > 0.03) or (direction == "short" and fr < -0.03):
            total_score -= 4 * 0.5
            signals_detail.append("费率反向")
    except:
        pass

    # ---- 6. 主动买盘比率（7分）----
    taker_ratio = coinglass_data.get("taker_ratio", "N/A")
    try:
        tr = float(taker_ratio)
        if direction == "long":
            s = linear_score(tr, 0.5, 0.65, 7, reverse=False)
        else:
            s = linear_score(tr, 0.35, 0.5, 7, reverse=True)
        total_score += s
        if s > 2:
            signals_detail.append(f"主动买盘({tr:.2f})")
        if (direction == "long" and tr < 0.45) or (direction == "short" and tr > 0.55):
            total_score -= 7 * 0.5
            signals_detail.append("主动方向反向")
    except:
        pass

    # ---- 7. 净持仓累积（5分）----
    net_pos = coinglass_data.get("net_position_cum", "N/A")
    try:
        np = float(net_pos)
        if direction == "long":
            s = linear_score(np, 500, 2000, 5, reverse=False)
        else:
            s = linear_score(np, -2000, -500, 5, reverse=True)
        total_score += s
        if abs(s) > 1:
            signals_detail.append(f"净持仓({np:.0f})")
        if (direction == "long" and np < -500) or (direction == "short" and np > 500):
            total_score -= 5 * 0.5
            signals_detail.append("净持仓反向")
    except:
        pass

    # ---- 8. 订单簿失衡率（11分）----
    imbalance = coinglass_data.get("orderbook_imbalance", 0.0)
    if direction == "long":
        s = linear_score(imbalance, 0.1, 0.3, 11, reverse=False)
    else:
        s = linear_score(imbalance, -0.3, -0.1, 11, reverse=True)
    total_score += s
    if abs(s) > 3:
        signals_detail.append(f"订单簿({imbalance:.2f})")
    if (direction == "long" and imbalance < -0.15) or (direction == "short" and imbalance > 0.15):
        total_score -= 11 * 0.4
        signals_detail.append("订单簿反向")

    # ---- 9. ETH/BTC 汇率趋势（8分）----
    if eth_btc_data:
        trend = eth_btc_data.get("trend", "neutral")
        if direction == "long" and trend == "up":
            total_score += 8
            signals_detail.append(f"ETH/BTC上升(+8)")
        elif direction == "short" and trend == "down":
            total_score += 8
            signals_detail.append(f"ETH/BTC下降(+8)")
        elif (direction == "long" and trend == "down") or (direction == "short" and trend == "up"):
            total_score -= 4
            signals_detail.append(f"ETH/BTC逆向(-4)")

    # ---- 10. 交易所钱包余额（7分）----
    if balance_data:
        btc_flow = balance_data.get("btc_flow", "neutral")
        stable_flow = balance_data.get("stable_flow", "neutral")
        if direction == "long" and stable_flow == "in" and btc_flow == "out":
            total_score += 7
            signals_detail.append(f"余额:稳定币流入&BTC流出(+7)")
        elif direction == "short" and stable_flow == "out" and btc_flow == "in":
            total_score += 7
            signals_detail.append(f"余额:稳定币流出&BTC流入(+7)")
        elif (direction == "long" and stable_flow == "out" and btc_flow == "in") or \
             (direction == "short" and stable_flow == "in" and btc_flow == "out"):
            total_score -= 3
            signals_detail.append(f"余额逆向(-3)")

    na_count = sum(1 for v in [coinglass_data.get("above_short_liquidation"),
                               coinglass_data.get("top_long_short_ratio"),
                               coinglass_data.get("cvd_signal")] if v == "N/A")
    total_score -= min(8, na_count * 2)

    total_score = max(-20, min(100, total_score))
    
    if total_score >= 75:
        level = "极强"
    elif total_score >= 55:
        level = "强"
    elif total_score >= 35:
        level = "中"
    elif total_score >= 15:
        level = "弱"
    else:
        level = "極弱"

    win_rate = int(40 + (total_score / 100) * 45)
    win_rate = max(40, min(85, win_rate))

    if liq_zero_count >= 2:
        level = "極弱"
        total_score = max(0, total_score - 30)
        win_rate = max(35, win_rate - 20)

    return {
        "level": level,
        "score": round(total_score, 1),
        "max_score": 100,
        "details": signals_detail,
        "win_rate": win_rate
    }


def calculate_win_rate(symbol: str, direction: str, coinglass_data: dict, macro_data: dict, profile: dict, market_regime: dict = None, liq_zero_count: int = 0, eth_btc_data: dict = None, balance_data: dict = None) -> int:
    strength = calculate_signal_strength(symbol, direction, coinglass_data, macro_data, liq_zero_count, eth_btc_data, balance_data)
    return strength["win_rate"]


def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict, profile: dict, volatility_factor: float = 1.0, market_regime: dict = None, liq_warning: str = "", data_source_status: str = "") -> str:
    fg = macro_data.get("fear_greed", {})
    stop_rule = f"止损距离 = max({profile['stop_multiplier']} × ATR, 最近清算密集区距离 × 1.2)"
    position_rule = f"基准仓位 {profile['base_position']*100:.0f}%，最大 {profile['max_position']*100:.0f}%。"
    cluster = coinglass_data.get("nearest_cluster", {})
    cluster_direction = cluster.get("direction", "N/A")
    cluster_price_raw = cluster.get("price", "N/A")
    cluster_intensity = cluster.get("intensity", "N/A")
    option_pain = coinglass_data.get("skew", "N/A")
    liq_max_pain = coinglass_data.get("max_pain_price", "N/A")
    warning_text = f"\n{liq_warning}\n" if liq_warning else ""
    data_source_text = f"\n**{data_source_status}**\n" if data_source_status else ""

    return f"""你是一位精通**清算动力学、多空博弈和数据量化分析**的顶尖加密货币短线合约交易员。你必须严格遵循所有分析步骤，**不得跳过、简化或敷衍**。

⚠️ **特别警告**：如果你在`reasoning`中未能体现对清算数据、费率、宏观过滤器、止盈锚定的明确分析，你的输出将被视为无效。你的目标是给出一个专业交易员级别的、逻辑严密的策略，而不是一个泛泛而谈的建议。

{warning_text}
{data_source_text}

### 核心市场数据
**价格与波动**
- 当前价格：{price} USDT
- 1小时ATR：{atr} USDT
- 波动因子：{volatility_factor:.2f}

**清算压力**
- 上方空头清算：{coinglass_data.get('above_short_liquidation', 'N/A')} USD
- 下方多头清算：{coinglass_data.get('below_long_liquidation', 'N/A')} USD
- 清算最大痛点：{liq_max_pain} USDT
- 最近清算密集区：{cluster_direction}方 {cluster_price_raw} USDT，强度{cluster_intensity}/5

**多空博弈**
- 资金费率：{coinglass_data.get('funding_rate', 'N/A')}%
- 持仓量24h变化：{coinglass_data.get('oi_change_24h', 'N/A')}%
- 主动吃单比率：{coinglass_data.get('taker_ratio', 'N/A')}
- 顶级交易员多空比：{coinglass_data.get('top_long_short_ratio', 'N/A')}
- 净持仓累积：{coinglass_data.get('net_position_cum', 'N/A')}
- 订单簿失衡率：{coinglass_data.get('orderbook_imbalance', 0.0):.2f}

**资金流向**
- CVD信号：{coinglass_data.get('cvd_signal', 'N/A')}

**期权与宏观**
- 期权最大痛点：{option_pain} USDT
- 恐惧贪婪指数：{fg.get('value', '50')}

### 🔒 强制分析流程（必须逐项在reasoning中体现，否则视为无效策略）

在输出最终的JSON前，你必须在内心（并在reasoning字段中）完成以下步骤的确认。每一个步骤的结论都必须明确。

**第一步：清算动力学定锚**
- 对比上方空头清算总额和下方多头清算总额。
- **强制解读规则**：
    1. 若【下方多头清算 ≈ 0 或 规模极小】，意味着价格下方无强力支撑，一旦下跌极易崩盘。此时，即使上方有巨大空头清算，也**严禁**将此解读为【偏多】。必须标注为【风险预警：下方无支撑，做多盈亏比极差】。
    2. 只有当【下方多头清算 > 0】且【上方空头清算 > 下方多头清算 × 1.3】时，才可解读为【偏多】（支撑位明确，盈亏比合理）。
- 确认结果：【偏多/偏空/风险预警】

**第二步：多空博弈找“犯错方”**
- 分析资金费率：是极端正值（多头拥挤）还是负值（空头拥挤）？
- 分析顶级交易员仓位：是净多（<0.7）还是净空（>2.0）？
- 分析净持仓累积：是持续净多头还是净空头？
- 判断结论：当前市场哪一方（多头/空头）承受的压力更大，更容易成为“踩踏”对象？
- 确认结果：【偏多/偏空/中性】

**第三步：宏观过滤器定基调**
- 分析ETH/BTC汇率趋势：是上升（风险偏好回暖）还是下降（避险）？
- 分析交易所钱包余额：稳定币和BTC的流向组合是（看涨/看跌/中性）？
- 判断结论：当前宏观资金流向是（支持/反对/中性）第一步和第二步的方向？
- 确认结果：【支持/反对/中性】

**第四步：信号共振与矛盾裁决**
- 列举所有支持最终方向的信号。
- 列举所有与最终方向矛盾的信号。
- 最终裁决：共振信号的强度是否足以压倒矛盾信号？若矛盾信号≥2个且共振强度<55分，应输出neutral。

**第五步：止盈止损锚定与盈亏比计算（强制输出）**
- TP1锚定来源：____（清算区/期权痛点/ATR估算），该锚点与当前价的距离是____ USDT。
- 止损锚定来源：____，与当前价的距离是____ USDT。
- **计算盈亏比**：做多时，盈亏比 = (TP1 - 入场价) / (入场价 - 止损)。做空时，盈亏比 = (入场价 - TP1) / (止损 - 入场价)。
- **强制输出盈亏比数值**：你必须在JSON的`profit_ratio`字段中输出计算结果（保留两位小数）。
- **硬性风控底线**：若计算出的盈亏比 < 0.3，必须输出`direction: "neutral"`，并在`reasoning`中说明盈亏比过低。
- **风险警告**：若盈亏比在0.3-0.8之间，必须在`risk_note`中明确警告：“盈亏比仅为X.XX，潜在亏损大于潜在盈利，请谨慎评估。”

**完成以上分析后，再生成最终JSON。你的reasoning字段必须是对上述步骤的简要总结。**

### 策略输出要求
请严格按JSON格式输出：
{{
  "direction": "long" 或 "short" 或 "neutral",
  "confidence": "high" 或 "medium" 或 "low",
  "entry_price_low": 入场区间下限,
  "entry_price_high": 入场区间上限,
  "stop_loss": 止损价,
  "take_profit_1": 第一止盈价,
  "tp1_anchor": "TP1锚定来源",
  "take_profit_2": 第二止盈价,
  "tp2_anchor": "TP2锚定来源",
  "position_size_ratio": 仓位比例（0.0-1.0）,
  "profit_ratio": 盈亏比数值（保留两位小数）,
  "reasoning": "必须包含强制分析五步骤的简要结论",
  "risk_note": "风险提示（若盈亏比<0.8必须明确警告）"
}}

### 止盈锚定原则
- TP1 优先锚定最近清算密集区（强度≥3/5）或期权最大痛点，且盈利空间需≥0.8×ATR且≤3×ATR。
- TP2 锚定下一个清算区或清算最大痛点，需与TP1保持分层距离。
- 若有效锚点不足，可使用1.5×ATR估算。

### 止损与仓位
- {stop_rule}
- {position_rule}
- 所有价格保留1位小数。
"""


def call_deepseek(prompt: str, max_retries: int = 2) -> dict:
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1000)
            content = response.choices[0].message.content
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start == -1 or json_end == 0:
                raise ValueError("未找到 JSON")
            json_str = content[json_start:json_end]
            strategy = json.loads(json_str)
            strategy.setdefault("win_rate", 0)
            strategy.setdefault("tp1_anchor", "未提供")
            strategy.setdefault("tp2_anchor", "未提供")
            strategy.setdefault("is_probe", False)
            strategy.setdefault("profit_ratio", 0.0)
            return strategy
        except Exception as e:
            logger.warning(f"DeepSeek 调用失败: {e}")
            if attempt == max_retries - 1: raise
    return {}


def validate_strategy(strategy: dict, current_price: float) -> bool:
    if "direction" not in strategy: return False
    direction = strategy["direction"]
    if direction not in ["long", "short", "neutral"]: return False
    if direction == "neutral": return True
    required = ["entry_price_low", "entry_price_high", "stop_loss"]
    for field in required:
        if field not in strategy or strategy[field] in [None, ""]: return False
        try: float(strategy[field])
        except: return False
    entry_low = float(strategy["entry_price_low"])
    entry_high = float(strategy["entry_price_high"])
    stop = float(strategy["stop_loss"])
    if direction == "long" and stop >= entry_low: return False
    if direction == "short" and stop <= entry_high: return False
    return True
