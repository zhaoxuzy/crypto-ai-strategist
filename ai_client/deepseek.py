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
    score, det = 0.0, []
    trend_score = trend_info.get("score", 0) if trend_info else 0

    # ---------- 震荡市权重（总和 100）----------
    w_liq_r = 28      # 清算结构
    w_pos_r = 16      # 持仓结构
    w_cvd_r = 18      # CVD
    w_fg_r = 4        # 恐惧贪婪
    w_fr_r = 5        # 资金费率
    w_taker_r = 14    # 主动吃单比率
    w_net_r = 7       # 净持仓变化
    w_ob_r = 5        # 订单簿失衡
    w_macro_r = 3     # 宏观（ETH/BTC、余额）

    # ---------- 趋势市权重（总和 100）----------
    w_liq_t = 22      # 清算结构
    w_pos_t = 10      # 持仓结构
    w_cvd_t = 25      # CVD
    w_fg_t = 3        # 恐惧贪婪
    w_fr_t = 4        # 资金费率
    w_taker_t = 20    # 主动吃单比率
    w_net_t = 5       # 净持仓变化
    w_ob_t = 5        # 订单簿失衡
    w_macro_t = 3     # 宏观

    # 根据趋势得分线性插值权重
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

    # ---------- 1. 清算结构 ----------
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

    # ---------- 2. 持仓结构 ----------
    pos_s, pos_d = get_position_structure_score(direction, cg, macro, symbol)
    score += pos_s * (weight_pos / 32.0)
    det.extend(pos_d)

    # ---------- 3. CVD ----------
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

    # ---------- 4. 恐惧贪婪（已移除额外扣分）----------
    fg_val = int(macro.get("fear_greed", {}).get("value", 50))
    if direction == "long":
        s = linear_score(fg_val, 20, 50, weight_fg, True)
    else:
        s = linear_score(fg_val, 50, 80, weight_fg, False)
    score += s
    if s > 2:
        det.append(f"恐惧贪婪({fg_val})")
    # 注意：已移除 fg_val < 30 时的 -10 分惩罚

    # ---------- 5. 资金费率 ----------
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

    # ---------- 6. 主动吃单比率 ----------
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

    # ---------- 7. 净持仓累积（OI百分比）----------
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

    # ---------- 8. 订单簿失衡率 ----------
    imb = cg.get("orderbook_imbalance", 0.0)
    if direction == "long":
        s = linear_score(imb, 0.1, 0.3, weight_ob, False)
    else:
        s = linear_score(imb, -0.3, -0.1, weight_ob, True)
    score += s
    if abs(s) > 3:
        det.append(f"订单簿({imb:.2f})")

    # ---------- 9. 宏观过滤器 ----------
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

    # 数据缺失扣分
    core_missing = sum(1 for v in [cg.get("above_short_liquidation"), cg.get("cvd_signal")] if v == "N/A")
    important_missing = sum(1 for v in [cg.get("top_long_short_ratio"), cg.get("funding_rate")] if v == "N/A")
    score -= min(15, core_missing * 5 + important_missing * 3)

    score = max(-20.0, min(100.0, score))

    # 信号强度等级
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

    # 置信度等级
    if score >= 60:
        confidence_grade = "High"
    elif score >= 35:
        confidence_grade = "Medium"
    else:
        confidence_grade = "Low"

    if liq_zero >= 2:
        level = "极弱"
        score = max(0, score - 30)
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
                 momentum_override: dict = None, key_levels: dict = None,
                 near_key_level: bool = False, directional_scores: dict = None,
                 stop_candidates: dict = None) -> str:
    fg = macro_data.get("fear_greed", {})
    cluster = coinglass_data.get("nearest_cluster", {})
    liq_max_pain = coinglass_data.get("max_pain_price", "N/A")
    option_pain = coinglass_data.get("skew", "N/A")
    warning_text = f"\n{liq_warning}\n" if liq_warning else ""
    data_source_text = f"\n**{data_source_status}**\n" if data_source_status else ""
    extreme_liq_text = ("\n⚠️ **极端清算警报**（系统判定：单侧清算额超过历史均值3倍）\n"
                        if extreme_liq else "")

    override_text = ""
    if momentum_override and momentum_override.get("active"):
        d = momentum_override.get("direction", "neutral")
        override_text = f"""
🚨🚨🚨 **最高优先级规则：动量追势信号已触发 - 强制覆盖模式** 🚨🚨🚨
- 你必须输出与信号一致的方向：**{d.upper()}**。
- 来源：价格{"<" if d == 'short' else ">"}EMA55 + CVD {momentum_override.get('cvd')} + 主动买卖盘确认。
- **唯一例外**：仅当 extreme_liq=true 时可输出 neutral。否则必须输出 {d}。
- **覆盖模式下，无视后续所有分析步骤中的矛盾信号。**
- 仓位建议：轻仓(正常仓位30%-50%)，止损0.8×ATR，止盈1.5×ATR。
"""

    support = key_levels.get("support", 0) if key_levels else 0
    resistance = key_levels.get("resistance", 0) if key_levels else 0
    near_text = ("⚠️ 价格紧贴关键位（距离<0.3×ATR），若输出方向，confidence 必须为 low，并注明轻仓。"
                 if near_key_level else "")

    bull_score = directional_scores.get("bull", 0) if directional_scores else 0
    bear_score = directional_scores.get("bear", 0) if directional_scores else 0
    diff = abs(bull_score - bear_score)
    if diff > 15:
        score_guidance = f"**方向倾向得分**：多头 {bull_score} vs 空头 {bear_score}。差值 {diff} > 15，可倾向高分方向（置信度 low）。"
    elif diff > 10:
        score_guidance = f"**方向倾向得分**：多头 {bull_score} vs 空头 {bear_score}。差值在10-15之间，弱倾向，若输出方向必须为试探信号(is_probe=true)。"
    else:
        score_guidance = f"**方向倾向得分**：多头 {bull_score} vs 空头 {bear_score}。差值较小，信号矛盾。"

    trend_desc = ""
    if trend_info:
        dir_t = trend_info.get('direction', 'neutral')
        score_t = trend_info.get('score', 0)
        conf_t = trend_info.get('confidence', '低')
        signals_t = ", ".join(trend_info.get('signals', []))
        trend_desc = f"**趋势强度**：{dir_t}倾向，得分{score_t}/100（可信度：{conf_t}）\n- 支持信号：{signals_t}"
        if 30 <= score_t <= 70:
            trend_desc += "\n⚠️ 市场处于震荡与趋势的过渡期，方向判定存在不确定性。"

    stop_override = stop_candidates.get("override", 0.0) if stop_candidates else 0.0
    stop_key = stop_candidates.get("key", 0.0) if stop_candidates else 0.0
    stop_default = stop_candidates.get("default", 0.0) if stop_candidates else 0.0

    return f"""你是一位精通**清算动力学、多空博弈和数据量化分析**的顶尖加密货币短线合约交易员。你必须严格遵循所有分析步骤，**不得跳过、简化或敷衍**。

⚠️ **特别警告**：如果你在`reasoning`中未能体现对清算数据、费率、宏观过滤器、止盈锚定的明确分析，你的输出将被视为无效。

{warning_text}
{data_source_text}
{extreme_liq_text}
{override_text}
{trend_desc}

### 核心市场数据
**价格与波动**
- 当前价格：{price} USDT
- 1小时ATR：{atr} USDT
- 波动因子：{volatility_factor:.2f}
- **系统计算关键支撑**：{support:.1f} USDT
- **系统计算关键阻力**：{resistance:.1f} USDT

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

**量化倾向参考**
- {score_guidance}
- {near_text}

### 🔒 强制分析流程（必须逐项在reasoning中体现）

**🚨 最高优先级规则**：若上方提示“动量追势信号已触发 - 强制覆盖模式”，你必须输出与信号一致的方向（short/long），不得输出 neutral。唯一例外是极端清算风险（extreme_liq=true）。覆盖模式下，后续分析步骤仅用于补充说明，不得改变方向。

**第一步：清算动力学定锚**
- 对比上下方清算金额。结合趋势强度得分（{trend_info.get('score',0) if trend_info else 0}）：若得分较高（>70），清算墙视为“猎物”而非“支撑/阻力”。
- 确认结果：【偏多/偏空/风险预警/中性观察】

**第二步：多空博弈找“犯错方”**
- 分析资金费率、顶级交易员多空比、净持仓累积。
- 结论：【偏多/偏空/中性】

**第三步：宏观过滤器定基调**
- 分析ETH/BTC汇率趋势、交易所钱包余额、恐惧贪婪指数。
- 结论：【支持/反对/中性】

**第四步：信号共振与矛盾裁决**
- 列举支持与矛盾信号。**必须提及最支持方向的信号和最矛盾的信号**。
- **倾向性裁决**：参考上方提供的方向倾向得分（多头{bull_score} vs 空头{bear_score}）。若差值>10分，且无覆盖指令，可倾向高分方向（confidence为low）。
- **过渡期规则**：趋势得分30-70时，允许输出试探信号（`is_probe: true`），前提是清算或持仓结构有明确偏向。
- **紧贴规则**：若价格紧贴关键位（距离<0.3×ATR），不得直接输出neutral。允许输出方向，但必须将`confidence`设为`low`，并在`risk_note`中注明“⚠️价格紧贴关键区，盈亏比偏窄，建议轻仓”。
- 若最终输出neutral，必须显式说明否决原因。

**第五步：止损设置（强制选择，不得修改数值）**
系统已为你计算好三个止损候选价，你必须按优先级选择**其中一个**，且**不得修改数值**：
1. 若动量覆盖模式激活 → 必须使用 `{stop_override:.1f}`
2. 否则，若有关键支撑/阻力 → 必须使用 `{stop_key:.1f}`
3. 否则 → 必须使用 `{stop_default:.1f}`
**在reasoning中只需注明选择的规则编号（如“规则1”），无需任何计算。止损价直接填入输出JSON的stop_loss字段。**

### 分批止盈规则（必须遵守）
- 触及 TP1 时，必须平仓 **50%** 的仓位，剩余仓位止损移动至成本价。
- TP2 为最终目标，触及后平仓剩余仓位。
- 在 `risk_note` 中必须注明：“TP1减仓50%，剩余仓位止损移至成本价”。
- **试探信号仓位限制**：若`is_probe: true`，建议仓位不得超过正常仓位的50%。

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
  "reasoning": "必须包含强制分析步骤的简要结论，并说明止损依据及所选规则编号",
  "risk_note": "风险提示（必须包含分批止盈说明）"
}}

### 止盈锚定原则
- TP1 优先锚定最近清算密集区（强度≥3/5）或期权最大痛点，且盈利空间需≥0.8×ATR且≤3×ATR。
- TP2 锚定下一个清算区或清算最大痛点，需与TP1保持分层距离。
- 若有效锚点不足，可使用1.5×ATR估算。
"""


def call_deepseek(prompt: str, momentum_override: dict = None, extreme_liq: bool = False,
                  max_retries: int = 2) -> dict:
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
    for _ in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000
            )
            content = resp.choices[0].message.content
            js = content[content.find('{'):content.rfind('}') + 1]
            s = json.loads(js)

            for k in ["tp1_anchor", "tp2_anchor", "is_probe"]:
                s.setdefault(k, "未提供" if "anchor" in k else False)

            if momentum_override and momentum_override.get("active") and not extreme_liq:
                req_dir = momentum_override.get("direction")
                if s.get("direction") != req_dir:
                    logger.warning(f"动量覆盖硬校验触发：AI输出 {s.get('direction')} 修正为 {req_dir}")
                    s["direction"] = req_dir
                    s["confidence"] = "medium"
                    s["is_probe"] = True
                    s["reasoning"] = f"[动量覆盖强制修正] {s.get('reasoning', '')}"

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