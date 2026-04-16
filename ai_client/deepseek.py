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


def get_position_structure_score(direction: str, coinglass_data: dict, macro_data: dict, symbol: str) -> tuple:
    score = 0.0
    details = []
    thresholds = {
        "BTC": {"long": 0.7, "short": 2.0},
        "ETH": {"long": 0.7, "short": 2.0},
        "SOL": {"long": 0.5, "short": 1.5}
    }
    th = thresholds.get(symbol.upper(), {"long": 0.7, "short": 2.0})
    
    top_ls = coinglass_data.get("top_long_short_ratio", "N/A")
    try:
        tls = float(top_ls)
        if direction == "long":
            if tls <= th["long"]:
                s = 20.0
            elif tls <= th["short"]:
                s = linear_score(tls, th["long"], th["short"], 20, reverse=True)
            else:
                s = 0.0
        else:
            if tls >= th["short"]:
                s = 20.0
            elif tls >= th["long"]:
                s = linear_score(tls, th["long"], th["short"], 20, reverse=False)
            else:
                s = 0.0
        score += s
        if s > 1:
            details.append(f"顶级持仓结构({tls:.2f})")
        elif tls != "N/A" and ((direction == "long" and tls > th["short"]) or (direction == "short" and tls < th["long"])):
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


def calculate_signal_strength(symbol: str, direction: str, coinglass_data: dict, macro_data: dict, liq_zero_count: int = 0, eth_btc_data: dict = None, balance_data: dict = None, trend_info: dict = None, extreme_liq: bool = False) -> dict:
    total_score = 0.0
    signals_detail = []
    min_liq_threshold = LIQ_MIN_THRESHOLDS.get(symbol.upper(), 50_000_000)

    trend_score = trend_info.get("score", 0) if trend_info else 0
    trend_direction = trend_info.get("direction", "neutral") if trend_info else "neutral"

    w_liq_r = 25
    w_pos_r = 29
    w_cvd_r = 11
    w_fg_r = 7
    w_fr_r = 4
    w_taker_r = 7
    w_net_r = 5
    w_ob_r = 11
    w_macro_r = 8

    w_liq_t = 15
    w_pos_t = 15
    w_cvd_t = 25
    w_fg_t = 5
    w_fr_t = 3
    w_taker_t = 15
    w_net_t = 3
    w_ob_t = 7
    w_macro_t = 5

    t = trend_score / 100.0
    weight_liq = int(w_liq_r * (1 - t) + w_liq_t * t)
    weight_pos = int(w_pos_r * (1 - t) + w_pos_t * t)
    weight_cvd = int(w_cvd_r * (1 - t) + w_cvd_t * t)
    weight_fg = int(w_fg_r * (1 - t) + w_fg_t * t)
    weight_fr = int(w_fr_r * (1 - t) + w_fr_t * t)
    weight_taker = int(w_taker_r * (1 - t) + w_taker_t * t)
    weight_net = int(w_net_r * (1 - t) + w_net_t * t)
    weight_ob = int(w_ob_r * (1 - t) + w_ob_t * t)
    weight_macro = int(w_macro_r * (1 - t) + w_macro_t * t)

    if extreme_liq:
        if direction == "long":
            total_score -= 50
            signals_detail.append("⚠️极端清算风险，禁止做多")
        elif direction == "short":
            total_score += 10
            signals_detail.append("极端清算支持做空")

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
                raw_s = linear_score(short_ratio, 0.5, 0.8, weight_liq, reverse=False)
            else:
                raw_s = linear_score(short_ratio, 0.2, 0.5, weight_liq, reverse=True)
            s = raw_s * scale_factor
            total_score += s
            if s > 5:
                if scale_factor < 0.5:
                    signals_detail.append(f"清算结构({short_ratio:.1%}, 规模小)")
                else:
                    signals_detail.append(f"清算结构({short_ratio:.1%})")
            if direction == "long" and below_val == 0 and trend_score < 50:
                total_score -= 15
                signals_detail.append("下方无多头清算(风险极大)")
            if (direction == "long" and short_ratio > 0.6) or (direction == "short" and short_ratio < 0.4):
                if not (trend_direction == "bear" and direction == "short"):
                    total_score -= weight_liq * 0.4
                    signals_detail.append("清算结构反向")
    except:
        pass

    pos_score, pos_details = get_position_structure_score(direction, coinglass_data, macro_data, symbol)
    total_score += pos_score * (weight_pos / 32)
    signals_detail.extend(pos_details)

    cvd = coinglass_data.get("cvd_signal", "N/A")
    if cvd in ["bullish", "slightly_bullish"]:
        if direction == "long":
            s = weight_cvd if cvd == "bullish" else weight_cvd * 0.7
            total_score += s
            signals_detail.append(f"CVD:{cvd}")
        else:
            total_score -= weight_cvd * 0.5
            signals_detail.append("CVD反向")
    elif cvd in ["bearish", "slightly_bearish"]:
        if direction == "short":
            s = weight_cvd if cvd == "bearish" else weight_cvd * 0.7
            total_score += s
            signals_detail.append(f"CVD:{cvd}")
        else:
            total_score -= weight_cvd * 0.5
            signals_detail.append("CVD反向")

    fg = macro_data.get("fear_greed", {})
    fg_val = int(fg.get("value", 50))
    if direction == "long":
        s = linear_score(fg_val, 20, 50, weight_fg, reverse=True)
    else:
        s = linear_score(fg_val, 50, 80, weight_fg, reverse=False)
    total_score += s
    if s > 2:
        signals_detail.append(f"恐惧贪婪({fg_val})")
    if (direction == "long" and fg_val > 70) or (direction == "short" and fg_val < 30):
        total_score -= weight_fg * 0.4
        signals_detail.append("情绪反向")

    funding_rate = coinglass_data.get("funding_rate", "N/A")
    try:
        fr = float(funding_rate)
        if direction == "short":
            s = linear_score(fr, 0.02, 0.08, weight_fr, reverse=False)
        else:
            s = linear_score(fr, -0.08, -0.01, weight_fr, reverse=True)
        total_score += s
        if abs(s) > 1:
            signals_detail.append(f"资金费率({fr:.4f})")
        if (direction == "long" and fr > 0.03) or (direction == "short" and fr < -0.03):
            total_score -= weight_fr * 0.5
            signals_detail.append("费率反向")
    except:
        pass

    taker_ratio = coinglass_data.get("taker_ratio", "N/A")
    try:
        tr = float(taker_ratio)
        if direction == "long":
            s = linear_score(tr, 0.5, 0.65, weight_taker, reverse=False)
        else:
            s = linear_score(tr, 0.35, 0.5, weight_taker, reverse=True)
        total_score += s
        if s > 2:
            signals_detail.append(f"主动买盘({tr:.2f})")
        if (direction == "long" and tr < 0.45) or (direction == "short" and tr > 0.55):
            total_score -= weight_taker * 0.5
            signals_detail.append("主动方向反向")
    except:
        pass

    net_pos = coinglass_data.get("net_position_cum", "N/A")
    total_oi_usd = coinglass_data.get("option_oi_usd", 0)
    try:
        total_oi = float(total_oi_usd) if total_oi_usd != "N/A" else 0
    except:
        total_oi = 0
    try:
        np = float(net_pos)
        net_pct = (np / total_oi * 100) if total_oi > 0 else 0.0
        if direction == "long":
            s = linear_score(net_pct, 1.0, 3.0, weight_net, reverse=False)
        else:
            s = linear_score(net_pct, -3.0, -1.0, weight_net, reverse=True)
        total_score += s
        if abs(s) > 1:
            signals_detail.append(f"净持仓({net_pct:.1f}%)")
        if (direction == "long" and net_pct < -1.0) or (direction == "short" and net_pct > 1.0):
            total_score -= weight_net * 0.5
            signals_detail.append("净持仓反向")
    except:
        pass

    imbalance = coinglass_data.get("orderbook_imbalance", 0.0)
    if direction == "long":
        s = linear_score(imbalance, 0.1, 0.3, weight_ob, reverse=False)
    else:
        s = linear_score(imbalance, -0.3, -0.1, weight_ob, reverse=True)
    total_score += s
    if abs(s) > 3:
        signals_detail.append(f"订单簿({imbalance:.2f})")
    if (direction == "long" and imbalance < -0.15) or (direction == "short" and imbalance > 0.15):
        total_score -= weight_ob * 0.4
        signals_detail.append("订单簿反向")

    if eth_btc_data:
        trend = eth_btc_data.get("trend", "neutral")
        if direction == "long" and trend == "up":
            total_score += weight_macro
            signals_detail.append(f"ETH/BTC上升(+{weight_macro})")
        elif direction == "short" and trend == "down":
            total_score += weight_macro
            signals_detail.append(f"ETH/BTC下降(+{weight_macro})")
        elif (direction == "long" and trend == "down") or (direction == "short" and trend == "up"):
            total_score -= weight_macro * 0.5
            signals_detail.append(f"ETH/BTC逆向(-{weight_macro*0.5:.0f})")

    if balance_data:
        btc_flow = balance_data.get("btc_flow", "neutral")
        stable_flow = balance_data.get("stable_flow", "neutral")
        if direction == "long" and stable_flow == "in" and btc_flow == "out":
            total_score += weight_macro
            signals_detail.append(f"余额:稳定币流入&BTC流出(+{weight_macro})")
        elif direction == "short" and stable_flow == "out" and btc_flow == "in":
            total_score += weight_macro
            signals_detail.append(f"余额:稳定币流出&BTC流入(+{weight_macro})")
        elif (direction == "long" and stable_flow == "out" and btc_flow == "in") or \
             (direction == "short" and stable_flow == "in" and btc_flow == "out"):
            total_score -= weight_macro * 0.4
            signals_detail.append(f"余额逆向(-{weight_macro*0.4:.0f})")

    core_missing = sum(1 for v in [coinglass_data.get("above_short_liquidation"),
                                   coinglass_data.get("cvd_signal")] if v == "N/A")
    important_missing = sum(1 for v in [coinglass_data.get("top_long_short_ratio"),
                                        coinglass_data.get("funding_rate")] if v == "N/A")
    auxiliary_missing = sum(1 for v in [coinglass_data.get("skew"),
                                        coinglass_data.get("option_oi_usd")] if v == "N/A")
    total_score -= min(15, core_missing * 5 + important_missing * 3 + auxiliary_missing * 1)

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
        level = "极弱"

    if total_score <= 30:
        win_rate = int(35 + total_score * 0.3)
    elif total_score <= 60:
        win_rate = int(45 + (total_score - 30) * 0.6)
    else:
        win_rate = int(63 + (total_score - 60) * 0.3)
    win_rate = max(35, min(85, win_rate))

    if liq_zero_count >= 2:
        level = "极弱"
        total_score = max(0, total_score - 30)
        win_rate = max(35, win_rate - 20)

    return {
        "level": level,
        "score": round(total_score, 1),
        "max_score": 100,
        "details": signals_detail,
        "win_rate": win_rate
    }


def calculate_win_rate(symbol: str, direction: str, coinglass_data: dict, macro_data: dict, profile: dict, trend_info: dict = None, liq_zero_count: int = 0, eth_btc_data: dict = None, balance_data: dict = None, extreme_liq: bool = False) -> int:
    strength = calculate_signal_strength(symbol, direction, coinglass_data, macro_data, liq_zero_count, eth_btc_data, balance_data, trend_info, extreme_liq)
    return strength["win_rate"]


def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict, profile: dict, volatility_factor: float = 1.0, trend_info: dict = None, extreme_liq: bool = False, liq_warning: str = "", data_source_status: str = "") -> str:
    fg = macro_data.get("fear_greed", {})
    stop_rule = f"止损距离 = max({profile['stop_multiplier']} × ATR, 最近清算密集区距离 × 1.2)"
    cluster = coinglass_data.get("nearest_cluster", {})
    cluster_direction = cluster.get("direction", "N/A")
    cluster_price_raw = cluster.get("price", "N/A")
    cluster_intensity = cluster.get("intensity", "N/A")
    option_pain = coinglass_data.get("skew", "N/A")
    liq_max_pain = coinglass_data.get("max_pain_price", "N/A")
    warning_text = f"\n{liq_warning}\n" if liq_warning else ""
    data_source_text = f"\n**{data_source_status}**\n" if data_source_status else ""

    extreme_liq_text = ""
    if extreme_liq:
        extreme_liq_text = "\n⚠️ **极端清算警报**：当前单侧清算额异常巨大，存在极端失衡风险。\n"

    trend_desc = ""
    if trend_info:
        direction = trend_info.get("direction", "neutral")
        score = trend_info.get("score", 0)
        confidence = trend_info.get("confidence", "低")
        signals = ", ".join(trend_info.get("signals", []))
        if direction == "bull":
            trend_desc = f"**趋势强度**：多头倾向，得分{score}/100（可信度：{confidence}）\n- 支持信号：{signals}"
        elif direction == "bear":
            trend_desc = f"**趋势强度**：空头倾向，得分{score}/100（可信度：{confidence}）\n- 支持信号：{signals}"
        else:
            trend_desc = f"**趋势强度**：无明显倾向，得分{score}/100（震荡特征明显）"
        if 30 <= score <= 70:
            trend_desc += "\n⚠️ 市场处于震荡与趋势的过渡期，方向判定存在不确定性。建议轻仓或等待确认。"

    return f"""你是一位精通**清算动力学、多空博弈和数据量化分析**的顶尖加密货币短线合约交易员。你必须严格遵循所有分析步骤，**不得跳过、简化或敷衍**。

⚠️ **特别警告**：如果你在`reasoning`中未能体现对清算数据、费率、宏观过滤器、止盈锚定的明确分析，你的输出将被视为无效。

{warning_text}
{data_source_text}
{extreme_liq_text}
{trend_desc}

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
  （注：强度≥3的清算区方可作为有效锚点）

**多空博弈**
- 资金费率：{coinglass_data.get('funding_rate', 'N/A')}%（绝对值<0.01%视为中性）
- 持仓量24h变化：{coinglass_data.get('oi_change_24h', 'N/A')}%
- 主动吃单比率：{coinglass_data.get('taker_ratio', 'N/A')}
- 顶级交易员多空比：{coinglass_data.get('top_long_short_ratio', 'N/A')}
- 净持仓累积：{coinglass_data.get('net_position_cum', 'N/A')}
- 订单簿失衡率：{coinglass_data.get('orderbook_imbalance', 0.0):.2f}（>0.2买盘占优，<-0.2卖盘占优）

**资金流向**
- CVD信号：{coinglass_data.get('cvd_signal', 'N/A')}

**期权与宏观**
- 期权最大痛点：{option_pain} USDT
- 恐惧贪婪指数：{fg.get('value', '50')}

### 🔒 强制分析流程（必须逐项在reasoning中体现）

**第一步：清算动力学定锚**
- 对比上下方清算金额。趋势强度得分较高时，清算墙视为“猎物”而非“支撑/阻力”。
- 结论应表述为【偏多/偏空/风险预警/中性观察】。

**第二步：多空博弈找“犯错方”**
- 分析资金费率、顶级交易员、净持仓。
- 结论：【偏多/偏空/中性】。

**第三步：宏观过滤器定基调**
- 分析ETH/BTC汇率趋势、交易所钱包余额。
- 结论：【支持/反对/中性】。

**第四步：信号共振与矛盾裁决**
- 列举支持与矛盾信号。必须提及最支持方向的信号和最矛盾的信号。
- 若最终输出neutral，必须显式说明否决原因。

**第五步：止损设置校验（强制执行）**
- 止损必须设在最近关键支撑/阻力外侧（做多时低于支撑0.2%-0.3%，做空时高于阻力0.2%-0.3%）。
- **必须在reasoning中注明止损依据**。
- 若无法找到明确关键位，必须输出neutral。

### 分批止盈规则（必须遵守）
- 触及 TP1 时，必须平仓 **50%** 的仓位，剩余仓位止损移动至成本价。
- TP2 为最终目标，触及后平仓剩余仓位。
- 在 `risk_note` 中必须注明：“TP1减仓50%，剩余仓位止损移至成本价”。

### 策略输出要求
请严格按JSON格式输出：
{{
  "direction": "long" 或 "short" 或 "neutral",
  "confidence": "high" 或 "medium" 或 "low",
  "is_probe": false 或 true,
  "entry_price_low": 入场区间下限,
  "entry_price_high": 入场区间上限,
  "stop_loss": 止损价,
  "take_profit_1": 第一止盈价,
  "tp1_anchor": "TP1锚定来源",
  "take_profit_2": 第二止盈价,
  "tp2_anchor": "TP2锚定来源",
  "reasoning": "必须包含强制分析步骤的简要结论，并说明止损依据",
  "risk_note": "风险提示（必须包含分批止盈说明）"
}}

### 止盈锚定原则
- TP1 优先锚定最近清算密集区（强度≥3/5）或期权最大痛点，且盈利空间需≥0.8×ATR且≤3×ATR。
- TP2 锚定下一个清算区或清算最大痛点，需与TP1保持分层距离。
- 若有效锚点不足，可使用1.5×ATR估算。

### 止损规则
- {stop_rule}
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
