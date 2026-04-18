import os
import json
from openai import OpenAI
from utils.logger import logger

def linear_score(v: float, low: float, high: float, full: float, rev: bool = False) -> float:
    if low == high: return 0.0
    if rev: return full if v <= low else (0.0 if v >= high else full * (high - v) / (high - low))
    else: return 0.0 if v <= low else (full if v >= high else full * (v - low) / (high - low))


def get_position_structure_score(direction: str, cg: dict, macro: dict, sym: str) -> tuple:
    s, det = 0.0, []
    th = {"BTC": (0.7, 2.0), "ETH": (0.7, 2.0), "SOL": (0.5, 1.5)}.get(sym.upper(), (0.7, 2.0))
    try:
        tls = float(cg.get("top_long_short_ratio", 1))
        if direction == "long":
            if tls <= th[0]: s += 20.0
            elif tls <= th[1]: s += linear_score(tls, th[0], th[1], 20, True)
        else:
            if tls >= th[1]: s += 20.0
            elif tls >= th[0]: s += linear_score(tls, th[0], th[1], 20, False)
        if s > 1: det.append(f"顶级持仓({tls:.2f})")
    except: pass
    try:
        lsa = float(cg.get("ls_account_ratio", 1))
        if direction == "long":
            if lsa <= 0.7: s += 12.0
            elif lsa <= 2.0: s += linear_score(lsa, 0.7, 2.0, 12, True)
        else:
            if lsa >= 2.0: s += 12.0
            elif lsa >= 0.7: s += linear_score(lsa, 0.7, 2.0, 12, False)
        if s > 1: det.append(f"人数比({lsa:.2f})")
    except: pass
    return s, det


LIQ_MIN = {"BTC": 50_000_000, "ETH": 20_000_000, "SOL": 5_000_000}


def calculate_signal_strength(symbol: str, direction: str, cg: dict, macro: dict,
                              liq_zero: int = 0, eth_btc: dict = None, bal: dict = None,
                              trend_info: dict = None, extreme_liq: bool = False) -> dict:
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
        if direction == "long": score -= 50; det.append("⚠️极端清算禁止做多")
        elif direction == "short": score += 10; det.append("极端清算支持做空")

    above = float(str(cg.get("above_short_liquidation", "0")).replace(",", ""))
    below = float(str(cg.get("below_long_liquidation", "0")).replace(",", ""))
    total = above + below
    if total > 0:
        ratio = above / total
        raw = linear_score(ratio, 0.2, 0.5, weight_liq, True) if direction == "long" else linear_score(ratio, 0.5, 0.8, weight_liq, False)
        s = raw * min(1.0, total / LIQ_MIN.get(symbol.upper(), 50_000_000))
        score += s
        if s > 5: det.append(f"清算结构({ratio:.1%})")

    pos_s, pos_d = get_position_structure_score(direction, cg, macro, symbol)
    score += pos_s * (weight_pos / 32.0)
    det.extend(pos_d)

    cvd = cg.get("cvd_signal", "N/A")
    if cvd in ["bullish", "slightly_bullish"]:
        if direction == "long": score += weight_cvd if cvd == "bullish" else weight_cvd * 0.7; det.append(f"CVD:{cvd}")
        else: score -= weight_cvd * 0.5; det.append("CVD反向")
    elif cvd in ["bearish", "slightly_bearish"]:
        if direction == "short": score += weight_cvd if cvd == "bearish" else weight_cvd * 0.7; det.append(f"CVD:{cvd}")
        else: score -= weight_cvd * 0.5; det.append("CVD反向")

    fg_val = int(macro.get("fear_greed", {}).get("value", 50))
    s = linear_score(fg_val, 20, 50, weight_fg, True) if direction == "long" else linear_score(fg_val, 50, 80, weight_fg, False)
    score += s
    if s > 2: det.append(f"恐惧贪婪({fg_val})")

    try:
        fr = float(cg.get("funding_rate", 0))
        s = linear_score(fr, 0.02, 0.08, weight_fr, False) if direction == "short" else linear_score(fr, -0.08, -0.01, weight_fr, True)
        score += s
        if abs(s) > 1: det.append(f"费率({fr:.4f})")
    except: pass

    try:
        tr = float(cg.get("taker_ratio", 0.5))
        s = linear_score(tr, 0.5, 0.65, weight_taker, False) if direction == "long" else linear_score(tr, 0.35, 0.5, weight_taker, True)
        score += s
        if s > 2: det.append(f"主动买卖({tr:.2f})")
    except: pass

    try:
        np = float(cg.get("net_position_cum", 0))
        oi = float(cg.get("option_oi_usd", 1)) if cg.get("option_oi_usd", "N/A") != "N/A" else 1.0
        pct = (np / oi * 100) if oi > 0 else 0.0
        s = linear_score(pct, 1.0, 3.0, weight_net, False) if direction == "long" else linear_score(pct, -3.0, -1.0, weight_net, True)
        score += s
        if abs(s) > 1: det.append(f"净持仓({pct:.1f}%)")
    except: pass

    imb = cg.get("orderbook_imbalance", 0.0)
    s = linear_score(imb, 0.1, 0.3, weight_ob, False) if direction == "long" else linear_score(imb, -0.3, -0.1, weight_ob, True)
    score += s
    if abs(s) > 3: det.append(f"订单簿({imb:.2f})")

    if eth_btc:
        trend = eth_btc.get("trend", "neutral")
        if direction == "long" and trend == "up": score += weight_macro; det.append(f"ETH/BTC上升(+{weight_macro})")
        elif direction == "short" and trend == "down": score += weight_macro; det.append(f"ETH/BTC下降(+{weight_macro})")
    if bal:
        btc_flow, stable_flow = bal.get("btc_flow", "neutral"), bal.get("stable_flow", "neutral")
        if direction == "long" and stable_flow == "in" and btc_flow == "out": score += weight_macro; det.append(f"余额:稳定币流入&BTC流出(+{weight_macro})")
        elif direction == "short" and stable_flow == "out" and btc_flow == "in": score += weight_macro; det.append(f"余额:稳定币流出&BTC流入(+{weight_macro})")

    if fg_val < 30 and direction == "long": score -= 10; det.append("⚠️极度恐惧做多门槛提高")
    core_missing = sum(1 for v in [cg.get("above_short_liquidation"), cg.get("cvd_signal")] if v == "N/A")
    important_missing = sum(1 for v in [cg.get("top_long_short_ratio"), cg.get("funding_rate")] if v == "N/A")
    score -= min(15, core_missing * 5 + important_missing * 3)
    score = max(-20.0, min(100.0, score))
    level = "极强" if score >= 75 else ("强" if score >= 55 else ("中" if score >= 35 else ("弱" if score >= 15 else "极弱")))
    confidence_grade = "High" if score >= 60 else ("Medium" if score >= 35 else "Low")
    return {"level": level, "score": round(score, 1), "max_score": 100, "details": det, "confidence_grade": confidence_grade}


def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict,
                 profile: dict, volatility_factor: float = 1.0, trend_info: dict = None,
                 extreme_liq: bool = False, liq_warning: str = "", data_source_status: str = "",
                 directional_scores: dict = None, signal_grade: str = "B",
                 entry_candidates: dict = None) -> str:
    fg = macro_data.get("fear_greed", {})
    cluster = coinglass_data.get("nearest_cluster", {})
    liq_max_pain = coinglass_data.get("max_pain_price", "N/A")
    option_pain = coinglass_data.get("skew", "N/A")
    warning_text = f"\n{liq_warning}\n" if liq_warning else ""
    data_source_text = f"\n**{data_source_status}**\n" if data_source_status else ""
    extreme_liq_text = ("\n⚠️ **极端清算警报**（系统判定：单侧清算额超过历史均值3倍）\n" if extreme_liq else "")

    bull_score = directional_scores.get("bull", 0) if directional_scores else 0
    bear_score = directional_scores.get("bear", 0) if directional_scores else 0
    diff = abs(bull_score - bear_score)
    higher_direction = "多头" if bull_score > bear_score else "空头"

    macro_signals = directional_scores.get("macro_signals", []) if directional_scores else []
    macro_signal_lines = []
    for s in macro_signals:
        macro_signal_lines.append(f"- {s['text']}：{s['direction']}（权重{s['weight']}）")
    macro_signals_text = "\n".join(macro_signal_lines) if macro_signal_lines else "- 无明显信号"

    trend_desc = ""
    if trend_info:
        dir_t = trend_info.get('direction', 'neutral')
        score_t = trend_info.get('score', 0)
        conf_t = trend_info.get('confidence', '低')
        signals_t = ", ".join(trend_info.get('signals', []))
        trend_desc = f"**趋势强度**：{dir_t}倾向，得分{score_t}/100（可信度：{conf_t}）\n- 支持信号：{signals_t}"
        if 30 <= score_t <= 70: trend_desc += "\n⚠️ 市场处于震荡与趋势的过渡期，方向判定存在不确定性。"

    if entry_candidates is None:
        entry_candidates = {
            "rule1": {"low": 0.0, "high": 0.0, "anchor": "无"},
            "rule2": {"low": 0.0, "high": 0.0, "anchor": "无"},
            "rule3": {"low": 0.0, "high": 0.0, "anchor": "无"}
        }

    return f"""你是一位精通**清算动力学、多空博弈和数据量化分析**的顶尖加密货币短线合约交易员。你必须严格遵循以下五步分析流程，基于提供的数据做出独立、专业的决策。

⚠️ **核心要求**：
- 不得跳过任何步骤，每步必须给出明确结论。
- 系统提供的量化参考仅供辅助。
- **第四步中的“强制裁决规则”具有绝对最高优先级，你必须无条件执行，不得以任何主观理由否决。**

{warning_text}{data_source_text}{extreme_liq_text}{trend_desc}

### 核心市场数据
**价格与波动**
- 当前价格：{price} USDT
- 4小时ATR：{atr} USDT
- 波动因子：{volatility_factor:.2f}

**清算压力**
- 上方空头清算：{coinglass_data.get('above_short_liquidation', 'N/A')} USD
- 下方多头清算：{coinglass_data.get('below_long_liquidation', 'N/A')} USD
- 清算最大痛点：{liq_max_pain} USDT
- 最近清算密集区：{cluster.get('direction', 'N/A')}方 {cluster.get('price', 'N/A')} USDT，强度{cluster.get('intensity', 'N/A')}/5
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
- **宏观三因子信号**：
{macro_signals_text}

**量化参考（供辅助决策）**
- 方向倾向得分：多头 {bull_score} vs 空头 {bear_score}。当前{higher_direction}领先{diff}分。
- 系统信号评级参考：{signal_grade}（A=共振强烈，B=标准跟随，C=试探信号）

### 🔒 强制五步分析流程

**第一步：清算动力学定锚**
- 对比上下方清算金额与密集区强度。结合趋势强度得分（{trend_info.get('score',0) if trend_info else 0}）：若趋势得分≥70，清算墙视为可被突破的“猎物”；若<50，清算墙的支撑/阻力作用增强；50-70为过渡区。
- 结论：【偏多/偏空/风险预警/中性观察】

**第二步：多空博弈找“犯错方”**
- 分析资金费率、顶级交易员多空比、净持仓累积，找出可能被挤压的一方。
- 结论：【偏多/偏空/中性】

**第三步：宏观过滤器定基调**
- 系统已提供宏观三因子信号及其权重（恐惧贪婪权重4，Coinbase溢价权重3，稳定币权重3）。
- **强制裁决规则（你必须严格遵守）**：
  1. 计算多头方向的总权重：将所有标注“利多”或“偏多”的信号的权重相加。
  2. 计算空头方向的总权重：将所有标注“利空”或“偏空”的信号的权重相加。
  3. 比较多空总权重：
     - 若多头总权重 > 空头总权重 → 必须输出【支持多头】。
     - 若空头总权重 > 多头总权重 → 必须输出【支持空头】。
     - 若两者相等且均为0 → 输出【中性】。
     - 若两者相等但均>0 → 输出【中性】，但必须在reasoning中说明“多空信号均衡”。
- **严禁**：因信号矛盾或主观判断而输出与权重计算结果不符的结论。

**第四步：信号共振与矛盾裁决**
- 列举最支持某方向的信号和最矛盾的信号。
- 应用以下强制裁决规则。

**🚨 强制裁决规则（绝对优先级，唯一例外是 extreme_liq=true）**：
1. 若第一步结论为【偏多】，且方向倾向得分差值 ≥ **8分** → **必须**输出 **long**。
2. 若第一步结论为【偏空】，且差值 ≥ **8分** → **必须**输出 **short**。
3. 若第一步结论为【风险预警】，且差值 ≥ **12分** → **必须**输出领先方向。
4. 若第一步结论为【中性观察】，不触发强制裁决。

**⚠️ 铁律（违反以下任何一条将导致你的输出被判定为无效）**：
- 一旦满足上述任一强制裁决条件，你**无权**以“风险回报比”、“价格紧贴关键位”、“市场结构矛盾”、“风险第一原则”等**任何理由**拒绝执行。
- 你只能在 **extreme_liq=true** 时拒绝执行强制裁决，并在 reasoning 中明确说明“因极端清算否决”。
- **严禁**在满足强制裁决条件时输出 **neutral**。
- **违规示例（绝对禁止）**：
  - “虽然满足强制裁决条件，但风险回报比差，我输出 neutral。”
  - “强制裁决与微观结构矛盾，遵循风险第一原则，选择不执行。”
  - “差值达标但价格紧贴阻力，综合判断后观望。”

**第五步：止损、止盈与入场区间设置**
- **入场区间**：系统已提供三个候选区间（见下方），你必须按优先级选择一个，并在reasoning中注明所选规则。
  - 规则1（清算区锚定）：{entry_candidates['rule1']['low']:.1f} - {entry_candidates['rule1']['high']:.1f}（锚定：{entry_candidates['rule1']['anchor']}）
  - 规则2（关键位锚定）：{entry_candidates['rule2']['low']:.1f} - {entry_candidates['rule2']['high']:.1f}（锚定：{entry_candidates['rule2']['anchor']}）
  - 规则3（ATR追单）：{entry_candidates['rule3']['low']:.1f} - {entry_candidates['rule3']['high']:.1f}（锚定：{entry_candidates['rule3']['anchor']}）
- **止损**：
  - 做多：止损设在**下方**最近强度≥3的**多头清算区**外侧（价格×0.998）。若无，则使用 **2 × 4小时ATR** 止损（入场价 - 2×ATR）。
  - 做空：止损设在**上方**最近强度≥3的**空头清算区**外侧（价格×1.002）。若无，则使用 **2 × 4小时ATR** 止损（入场价 + 2×ATR）。
- **止盈**（单一目标，严格遵守以下铁律）：
  - **做多时**：止盈**必须**锚定**上方**最近强度≥3的**空头清算区**。若无，则使用 2:1盈亏比计算（入场价 + 2×(入场价 - 止损价)）。
  - **做空时**：止盈**必须**锚定**下方**最近强度≥3的**多头清算区**。若无，则使用 2:1盈亏比计算（入场价 - 2×(止损价 - 入场价)）。
  - **严禁**将同向清算区用于止盈（做多时用下方清算区止盈、做空时用上方清算区止盈）。
  - **严禁**以“距离过近”、“盈亏比不足”等理由修改清算区价格。若该清算区导致盈亏比过低（如<1:1），你应在 `risk_note` 中明确提示“盈亏比偏低，建议轻仓或观望”，但**止盈价必须如实填入清算区价格**。
  - **违规示例（绝对禁止）**：
    - “止盈锚定上方空头清算区86.7，同时止损锚定下方多头清算区86.7” → 同一清算区不能同时用于止损和止盈。
    - “止盈86.7，盈亏比约0.5:1” → 止盈必须高于入场价，盈亏比至少>1:1。
- 在reasoning中明确写出入场、止损、止盈的锚定来源及盈亏比。

### 策略输出格式（严格JSON）
{{
  "direction": "long" 或 "short" 或 "neutral",
  "entry_price_low": 入场区间下限,
  "entry_price_high": 入场区间上限,
  "stop_loss": 止损价,
  "take_profit": 止盈价,
  "tp_anchor": "止盈锚定来源说明",
  "reasoning": "按五步法详细描述推理过程，每步用【】标题。第五步注明入场、止损、止盈的所选规则。",
  "risk_note": "风险提示"
}}
"""


def call_deepseek(prompt: str, max_retries: int = 2) -> dict:
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
    for _ in range(max_retries):
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1200)
            content = resp.choices[0].message.content
            js = content[content.find('{'):content.rfind('}') + 1]
            s = json.loads(js)
            s.setdefault("tp_anchor", "未提供")
            return s
        except Exception as e:
            logger.warning(f"DeepSeek调用失败: {e}")
    return {}


def validate_strategy(s: dict, price: float) -> bool:
    if s.get("direction") not in ["long", "short", "neutral"]: return False
    if s["direction"] == "neutral": return True
    try:
        entry_low = float(s.get("entry_price_low", 0))
        entry_high = float(s.get("entry_price_high", 0))
        stop = float(s.get("stop_loss", 0))
        if s["direction"] == "long" and stop >= entry_low: return False
        if s["direction"] == "short" and stop <= entry_high: return False
    except: return False
    return True
