import os
import json
from openai import OpenAI
from utils.logger import logger

# ---------- 连续型评分辅助函数 ----------
def linear_score(v: float, low: float, high: float, full: float, rev: bool = False) -> float:
    if low == high:
        return 0.0
    if rev:
        if v <= low:
            return full
        if v >= high:
            return 0.0
        return full * (high - v) / (high - low)
    else:
        if v <= low:
            return 0.0
        if v >= high:
            return full
        return full * (v - low) / (high - low)


def get_position_structure_score(direction: str, cg: dict, macro: dict, sym: str) -> tuple:
    s, det = 0.0, []
    th = {"BTC": (0.7, 2.0), "ETH": (0.7, 2.0), "SOL": (0.5, 1.5)}.get(sym.upper(), (0.7, 2.0))

    try:
        tls = float(cg.get("top_long_short_ratio", 1))
        if direction == "long":
            if tls <= th[0]:
                s += 20.0
            elif tls <= th[1]:
                s += linear_score(tls, th[0], th[1], 20, True)
        else:
            if tls >= th[1]:
                s += 20.0
            elif tls >= th[0]:
                s += linear_score(tls, th[0], th[1], 20, False)
        if s > 1:
            det.append(f"顶级持仓({tls:.2f})")
    except Exception:
        pass

    try:
        lsa = float(cg.get("ls_account_ratio", 1))
        if direction == "long":
            if lsa <= 0.7:
                s += 12.0
            elif lsa <= 2.0:
                s += linear_score(lsa, 0.7, 2.0, 12, True)
        else:
            if lsa >= 2.0:
                s += 12.0
            elif lsa >= 0.7:
                s += linear_score(lsa, 0.7, 2.0, 12, False)
        if s > 1:
            det.append(f"人数比({lsa:.2f})")
    except Exception:
        pass
    return s, det


LIQ_MIN = {"BTC": 50_000_000, "ETH": 20_000_000, "SOL": 5_000_000}


def calculate_signal_strength(symbol: str, direction: str, cg: dict, macro: dict,
                              liq_zero: int = 0, eth_btc: dict = None, bal: dict = None,
                              trend_info: dict = None, extreme_liq: bool = False) -> dict:
    """
    传统信号强度评分，作为AI的量化参考之一。不直接决定方向。
    """
    score, det = 0.0, []
    trend_score = trend_info.get("score", 0) if trend_info else 0

    w_liq_r, w_pos_r, w_cvd_r, w_fg_r, w_fr_r, w_taker_r, w_net_r, w_ob_r, w_macro_r = 25, 29, 11, 7, 4, 7, 5, 11, 8
    w_liq_t, w_pos_t, w_cvd_t, w_fg_t, w_fr_t, w_taker_t, w_net_t, w_ob_t, w_macro_t = 15, 15, 25, 5, 3, 15, 3, 7, 5

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
            score -= 50
            det.append("⚠️极端清算禁止做多")
        elif direction == "short":
            score += 10
            det.append("极端清算支持做空")

    above = float(str(cg.get("above_short_liquidation", "0")).replace(",", ""))
    below = float(str(cg.get("below_long_liquidation", "0")).replace(",", ""))
    total = above + below
    if total > 0:
        ratio = above / total
        if direction == "long":
            raw = linear_score(ratio, 0.2, 0.5, weight_liq, True)
        else:
            raw = linear_score(ratio, 0.5, 0.8, weight_liq, False)
        scale = min(1.0, total / LIQ_MIN.get(symbol.upper(), 50_000_000))
        s = raw * scale
        score += s
        if s > 5:
            det.append(f"清算结构({ratio:.1%})")

    pos_s, pos_d = get_position_structure_score(direction, cg, macro, symbol)
    score += pos_s * (weight_pos / 32.0)
    det.extend(pos_d)

    cvd = cg.get("cvd_signal", "N/A")
    if cvd in ["bullish", "slightly_bullish"]:
        if direction == "long":
            s = weight_cvd if cvd == "bullish" else weight_cvd * 0.7
            score += s
            det.append(f"CVD:{cvd}")
        else:
            score -= weight_cvd * 0.5
            det.append("CVD反向")
    elif cvd in ["bearish", "slightly_bearish"]:
        if direction == "short":
            s = weight_cvd if cvd == "bearish" else weight_cvd * 0.7
            score += s
            det.append(f"CVD:{cvd}")
        else:
            score -= weight_cvd * 0.5
            det.append("CVD反向")

    fg_val = int(macro.get("fear_greed", {}).get("value", 50))
    if direction == "long":
        s = linear_score(fg_val, 20, 50, weight_fg, True)
    else:
        s = linear_score(fg_val, 50, 80, weight_fg, False)
    score += s
    if s > 2:
        det.append(f"恐惧贪婪({fg_val})")

    try:
        fr = float(cg.get("funding_rate", 0))
        if direction == "short":
            s = linear_score(fr, 0.02, 0.08, weight_fr, False)
        else:
            s = linear_score(fr, -0.08, -0.01, weight_fr, True)
        score += s
        if abs(s) > 1:
            det.append(f"费率({fr:.4f})")
    except Exception:
        pass

    try:
        tr = float(cg.get("taker_ratio", 0.5))
        if direction == "long":
            s = linear_score(tr, 0.5, 0.65, weight_taker, False)
        else:
            s = linear_score(tr, 0.35, 0.5, weight_taker, True)
        score += s
        if s > 2:
            det.append(f"主动买卖({tr:.2f})")
    except Exception:
        pass

    try:
        np = float(cg.get("net_position_cum", 0))
        oi_usd = cg.get("option_oi_usd", "N/A")
        oi = float(oi_usd) if oi_usd != "N/A" else 1.0
        pct = (np / oi * 100) if oi > 0 else 0.0
        if direction == "long":
            s = linear_score(pct, 1.0, 3.0, weight_net, False)
        else:
            s = linear_score(pct, -3.0, -1.0, weight_net, True)
        score += s
        if abs(s) > 1:
            det.append(f"净持仓({pct:.1f}%)")
    except Exception:
        pass

    imb = cg.get("orderbook_imbalance", 0.0)
    if direction == "long":
        s = linear_score(imb, 0.1, 0.3, weight_ob, False)
    else:
        s = linear_score(imb, -0.3, -0.1, weight_ob, True)
    score += s
    if abs(s) > 3:
        det.append(f"订单簿({imb:.2f})")

    if eth_btc:
        trend = eth_btc.get("trend", "neutral")
        if direction == "long" and trend == "up":
            score += weight_macro
            det.append(f"ETH/BTC上升(+{weight_macro})")
        elif direction == "short" and trend == "down":
            score += weight_macro
            det.append(f"ETH/BTC下降(+{weight_macro})")
    if bal:
        btc_flow = bal.get("btc_flow", "neutral")
        stable_flow = bal.get("stable_flow", "neutral")
        if direction == "long" and stable_flow == "in" and btc_flow == "out":
            score += weight_macro
            det.append(f"余额:稳定币流入&BTC流出(+{weight_macro})")
        elif direction == "short" and stable_flow == "out" and btc_flow == "in":
            score += weight_macro
            det.append(f"余额:稳定币流出&BTC流入(+{weight_macro})")

    if fg_val < 30 and direction == "long":
        score -= 10
        det.append("⚠️极度恐惧做多门槛提高")

    core_missing = sum(1 for v in [cg.get("above_short_liquidation"), cg.get("cvd_signal")] if v == "N/A")
    important_missing = sum(1 for v in [cg.get("top_long_short_ratio"), cg.get("funding_rate")] if v == "N/A")
    score -= min(15, core_missing * 5 + important_missing * 3)

    score = max(-20.0, min(100.0, score))

    if score >= 75:
        level = "极强"
    elif score >= 55:
        level = "强"
    elif score >= 35:
        level = "中"
    elif score >= 15:
        level = "弱"
    else:
        level = "极弱"

    if score >= 60:
        confidence_grade = "High"
    elif score >= 35:
        confidence_grade = "Medium"
    else:
        confidence_grade = "Low"

    return {
        "level": level,
        "score": round(score, 1),
        "max_score": 100,
        "details": det,
        "confidence_grade": confidence_grade
    }


def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict,
                 profile: dict, volatility_factor: float = 1.0, trend_info: dict = None,
                 extreme_liq: bool = False, liq_warning: str = "", data_source_status: str = "",
                 directional_scores: dict = None,
                 signal_grade: str = "B",
                 stop_candidates: dict = None,
                 tp_candidates: dict = None) -> str:
    """
    构建DeepSeek API提示词。AI作为资深分析师，严格遵循五步法自主决策。
    强制裁决规则已硬编码，防止AI在边缘场景滥用neutral。
    """
    fg = macro_data.get("fear_greed", {})
    cluster = coinglass_data.get("nearest_cluster", {})
    liq_max_pain = coinglass_data.get("max_pain_price", "N/A")
    option_pain = coinglass_data.get("skew", "N/A")
    warning_text = f"\n{liq_warning}\n" if liq_warning else ""
    data_source_text = f"\n**{data_source_status}**\n" if data_source_status else ""
    extreme_liq_text = ("\n⚠️ **极端清算警报**（系统判定：单侧清算额超过历史均值3倍）\n"
                        if extreme_liq else "")

    bull_score = directional_scores.get("bull", 0) if directional_scores else 0
    bear_score = directional_scores.get("bear", 0) if directional_scores else 0
    diff = abs(bull_score - bear_score)
    higher_direction = "多头" if bull_score > bear_score else "空头"

    trend_desc = ""
    if trend_info:
        dir_t = trend_info.get('direction', 'neutral')
        score_t = trend_info.get('score', 0)
        conf_t = trend_info.get('confidence', '低')
        signals_t = ", ".join(trend_info.get('signals', []))
        trend_desc = f"**趋势强度**：{dir_t}倾向，得分{score_t}/100（可信度：{conf_t}）\n- 支持信号：{signals_t}"
        if 30 <= score_t <= 70:
            trend_desc += "\n⚠️ 市场处于震荡与趋势的过渡期，方向判定存在不确定性。"

    stop_rule2 = stop_candidates.get("rule2", 0.0) if stop_candidates else 0.0
    stop_rule3 = stop_candidates.get("rule3", 0.0) if stop_candidates else 0.0

    tp1 = tp_candidates.get("tp1", 0.0) if tp_candidates else 0.0
    tp2 = tp_candidates.get("tp2", 0.0) if tp_candidates else 0.0
    tp1_anchor = tp_candidates.get("tp1_anchor", "未提供") if tp_candidates else "未提供"
    tp2_anchor = tp_candidates.get("tp2_anchor", "未提供") if tp_candidates else "未提供"

    entry_width = atr * 0.002  # 约0.2% ATR

    return f"""你是一位精通**清算动力学、多空博弈和数据量化分析**的顶尖加密货币短线合约交易员。你必须严格遵循以下五步分析流程，基于提供的数据做出独立、专业的决策。

⚠️ **核心要求**：
- 不得跳过任何步骤，每步必须给出明确结论。
- 系统提供的量化参考（方向倾向得分、信号评级）仅供辅助，你有权根据数据自主裁决。
- **特别注意**：第四步中的“强制裁决规则”具有最高优先级，你必须严格遵守，不得以“信号矛盾”为由逃避决策。

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
- 最近清算密集区：{cluster.get('direction', 'N/A')}方 {cluster.get('price', 'N/A')} USDT，强度{cluster.get('intensity', 'N/A')}/5
  （注：强度≥3的清算区方可作为有效锚点）
- ⚠️ **数据口径说明**：清算金额字段可能因统计范围限制显示为0，但“最近清算密集区”仍可反映局部清算堆积，两者口径不同。**判断支撑/阻力时，以“最近清算密集区”为准。**

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

**量化参考（供辅助决策）**
- 方向倾向得分：多头 {bull_score} vs 空头 {bear_score}。当前{higher_direction}领先{diff}分。
- 系统信号评级参考：{signal_grade}（A=共振强烈，B=标准跟随，C=试探信号）

### 🔒 强制五步分析流程

**第一步：清算动力学定锚**
- 对比上下方清算金额。结合趋势强度得分（{trend_info.get('score',0) if trend_info else 0}）：若得分较高（>70），清算墙视为“猎物”而非“支撑/阻力”。
- 结论：【偏多/偏空/风险预警/中性观察】

**第二步：多空博弈找“犯错方”**
- 分析资金费率、顶级交易员多空比、净持仓累积，找出市场中可能被挤压的一方。
- 结论：【偏多/偏空/中性】

**第三步：宏观过滤器定基调**
- 分析恐惧贪婪指数、ETH/BTC汇率趋势（若有）、交易所钱包余额（若有）。
- 结论：【支持/反对/中性】

**第四步：信号共振与矛盾裁决**
- 列举最支持某方向的信号（至少一个）和最矛盾的信号（至少一个）。
- 结合方向倾向得分和系统评级，做出最终裁决。

**🚨 强制裁决规则（你必须遵守，不得以“信号矛盾”为由输出neutral）**：
1. 若方向倾向得分差值 ≥ 5 分，且清算动力学倾向（第一步结论）与该方向一致，则**必须**输出该方向，置信度设为 low，is_probe=true。**严禁输出 neutral**。
2. 若价格紧贴关键位（距离<0.3×ATR），且清算动力学有明确倾向：
   - 若紧贴**下方支撑**且清算倾向为**偏多** → 必须输出 **long**，置信度 low，is_probe=true。
   - 若紧贴**上方阻力**且清算倾向为**偏空** → 必须输出 **short**，置信度 low，is_probe=true。
   - 若紧贴关键位但清算倾向与紧贴方向矛盾（如紧贴上方阻力但清算偏多），则**不得**应用本规则，需综合其他信号裁决。
3. 若趋势得分在30-70之间（过渡期），且清算动力学倾向明确，同时方向倾向得分差值 ≥ 3 分，则**必须**输出该方向，置信度设为 low，is_probe=true。**严禁输出 neutral**。
4. 只有当清算动力学为“中性观察”，且方向倾向得分差值 < 5 分，且不满足以上任何一条强制出手条件时，才允许输出 neutral。

**第五步：止损与止盈设置**
- **止损规则优先级**（你必须按顺序选择，注明规则编号）：
  - **规则1（同向强清算区外侧）**：
    - 做多时，同向清算区 = **下方**多头清算密集区（支撑）。若存在强度≥3的下方清算区，止损设于该区外侧（价格×0.998）。
    - 做空时，同向清算区 = **上方**空头清算密集区（阻力）。若存在强度≥3的上方清算区，止损设于该区外侧（价格×1.002）。
  - **规则2（1.5×ATR固定止损）**：使用系统提供的固定值 {stop_rule2:.1f}，**严禁自行计算或修改**。
  - **规则3（2×ATR宽止损）**：使用系统提供的固定值 {stop_rule3:.1f}，**严禁自行计算或修改**。
- **止损价必须直接使用候选值，不得添加任何缓冲或自行调整。**
- 止盈直接使用以下候选值：
  TP1：{tp1:.1f}（锚定：{tp1_anchor}）
  TP2：{tp2:.1f}（锚定：{tp2_anchor}）

### 策略输出格式（严格JSON）
{{
  "direction": "long" 或 "short" 或 "neutral",
  "confidence": "high" 或 "medium" 或 "low",
  "is_probe": true/false,
  "entry_price_low": {price - entry_width:.1f},
  "entry_price_high": {price + entry_width:.1f},
  "stop_loss": 止损价,
  "take_profit_1": {tp1:.1f},
  "tp1_anchor": "{tp1_anchor}",
  "take_profit_2": {tp2:.1f},
  "tp2_anchor": "{tp2_anchor}",
  "reasoning": "按五步法详细描述推理过程，每步用【】标题。第五步注明所选止损规则。",
  "risk_note": "风险提示（必须包含分批止盈说明：TP1减仓50%，剩余仓位止损移至成本价）"
}}
"""


def call_deepseek(prompt: str, max_retries: int = 2) -> dict:
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
    for _ in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1200
            )
            content = resp.choices[0].message.content
            js = content[content.find('{'):content.rfind('}') + 1]
            s = json.loads(js)
            for k in ["tp1_anchor", "tp2_anchor", "is_probe"]:
                s.setdefault(k, "未提供" if "anchor" in k else False)
            return s
        except Exception as e:
            logger.warning(f"DeepSeek调用失败: {e}")
    return {}


def validate_strategy(s: dict, price: float) -> bool:
    if s.get("direction") not in ["long", "short", "neutral"]:
        return False
    if s["direction"] == "neutral":
        return True
    try:
        entry_low = float(s.get("entry_price_low", 0))
        entry_high = float(s.get("entry_price_high", 0))
        stop = float(s.get("stop_loss", 0))
        if s["direction"] == "long" and stop >= entry_low:
            return False
        if s["direction"] == "short" and stop <= entry_high:
            return False
    except Exception:
        return False
    return True
