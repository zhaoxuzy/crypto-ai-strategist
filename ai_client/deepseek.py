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
                 entry_candidates: dict = None, exchange_balances: dict = None,
                 liq_dynamic_signals: list = None,
                 threshold_bull_bear: int = 8, threshold_warning: int = 12,
                 tp_candidates: dict = None) -> str:
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

    liq_dynamic_text = "、".join(liq_dynamic_signals) if liq_dynamic_signals else "无显著动态信号"

    bal_text = ""
    if exchange_balances:
        btc_flow = exchange_balances.get("btc_flow", "neutral")
        stable_flow = exchange_balances.get("stable_flow", "neutral")
        bal_text = f"BTC 24h净变动: {exchange_balances.get('btc_change', 0):.0f} ({btc_flow})，稳定币24h净变动: {exchange_balances.get('stable_change', 0):.0f} ({stable_flow})"

    trend_desc = ""
    if trend_info:
        dir_t = trend_info.get('direction', 'neutral')
        score_t = trend_info.get('score', 0)
        conf_t = trend_info.get('confidence', '低')
        signals_t = ", ".join(trend_info.get('signals', []))
        trend_desc = f"**趋势强度**：{dir_t}倾向，得分{score_t}/100（可信度：{conf_t}）\n- 支持信号：{signals_t}"
        if 30 <= score_t <= 70: trend_desc += "\n⚠️ 市场处于震荡与趋势的过渡期，方向判定存在不确定性。"

    eth_btc = coinglass_data.get("eth_btc_ratio", {})
    eth_btc_trend = eth_btc.get('trend', 'N/A')
    eth_btc_ratio = eth_btc.get('current_ratio', 0.0)

    raw_view = coinglass_data.get("raw_view", {})

    liq_profile = raw_view.get("liquidation_profile", [])
    liq_profile_lines = []
    for item in liq_profile[:15]:
        dir_symbol = "⬆️" if item["direction"] == "above" else "⬇️"
        liq_profile_lines.append(
            f"| {item['price']:.2f} | {dir_symbol} {item['effect']} | {item['intensity']:.2f} | {item['distance_atr']:+.2f} |"
        )
    liq_profile_table = "\n".join(liq_profile_lines) if liq_profile_lines else "无清算数据"

    top_3_zones = raw_view.get("top_3_liquidation_zones", [])
    top_3_lines = []
    for i, zone in enumerate(top_3_zones, 1):
        top_3_lines.append(
            f"{i}. {zone['price']:.2f} ({zone['effect']})，强度 {zone['intensity']:.2f}，距现价 {zone['distance_atr']:+.2f} ATR"
        )
    top_3_summary = "\n".join(top_3_lines) if top_3_lines else "无明显清算聚集区"

    cvd_valid = raw_view.get("cvd_valid", False)
    cvd_series = raw_view.get("cvd_series_1m", [])
    cvd_series_str = str(cvd_series) if cvd_valid else "数据无效"

    ls_valid = raw_view.get("ls_valid", False)
    ls_series = raw_view.get("ls_ratio_series_1h", [])
    ls_series_str = str(ls_series) if ls_valid else "数据无效"

    taker_series = raw_view.get("taker_ratio_series_1h", [])
    taker_series_str = str(taker_series) if taker_series else "无数据"

    quant_reference_section = f"""
### 📟 内部量化引擎输出（**仅供参考，AI 必须重新验证**）

⚠️ **警告：此分值为机器根据规则硬算得出，未经过上下文校验。你必须基于上方原始数据独立判断。**

- 机械计算得分倾向：多头 {bull_score} vs 空头 {bear_score}。当前{higher_direction}领先{diff}分。
- 机械评级参考：{signal_grade}（A=共振强烈，B=标准跟随，C=试探信号）
"""

    prompt = f"""【角色锚定与思维禁令】
你是一位精通**清算动力学、多空博弈、微观订单簿博弈与跨市场资金流**的顶级加密货币短线合约交易员。
你的核心思维铁律：**不要阅读或复述系统给你的标签，你必须像一个刚拿到原始数据的操盘手一样，对每一项指标进行“盲测”式的独立推导。**
**禁止使用模糊词汇**（如“偏多”、“动能较强”），必须替换为**精确的数值对比或边界定义**。

⚠️ **核心要求**：
1.  **数据孤立解构（逐个指标强制引用）**：
    - 请依次列出以下每项原始数据的具体数值：[请在此处插入具体数据字段，如：OI变动量、CVD差值、资金费率、主动买卖量比、清算热力图层级]。
    - **强制要求**：在分析每一项时，必须使用句式：“当前[指标名称]的**具体读数为[X]**，相较于前一个周期的[Y]发生了[具体变化百分比或绝对值变化]。”

2.  **多空博弈冲突研判（裁决机制）**：
    - 识别上述指标中存在的**矛盾信号**（例如：资金费率负值但CVD显示承接）。
    - **裁决要求**：你拥有最高裁决权。如果系统给出的初步建议与你的数值推演相悖，**你必须明确指出**：“系统建议倾向于[多/空]，但我基于[某关键指标的具体数值]认为此逻辑在30-60分钟级别存在失效风险，理由如下：[结合清算地图点位或OI堆积位置说明]。”

3.  **时效性约束下的策略输出（反脆弱设计）**：
    - 你的分析必须规避“30分钟后即失效”的噪音行情。
    - **强制视角**：请重点关注**15分钟级别与1小时级别的共振或背离**。你的策略应明确回答：“**在当前第[X]根K线收盘确认前，[某价位]是不可证伪的博弈边界。**”

{warning_text}{data_source_text}{extreme_liq_text}{trend_desc}

### 核心数据

**价格与波动**
- 当前价格：{price} USDT
- 4小时ATR：{atr} USDT
- 波动因子：{volatility_factor:.2f}

**清算压力**
- 上方空头清算：{coinglass_data.get('above_short_liquidation', 'N/A')} USD
- 下方多头清算：{coinglass_data.get('below_long_liquidation', 'N/A')} USD
- 清算最大痛点：{liq_max_pain} USDT
- 最近清算密集区：{cluster.get('direction', 'N/A')}方 {cluster.get('price', 'N/A')} USDT，强度{cluster.get('intensity', 'N/A')}/5
- **清算动态信号**：{liq_dynamic_text}

**多空博弈**
- 资金费率：{coinglass_data.get('funding_rate', 'N/A')}%
- 持仓量24h变化：{coinglass_data.get('oi_change_24h', 'N/A')}%
- 主动吃单比率：{coinglass_data.get('taker_ratio', 'N/A')}
- 顶级交易员多空比：{coinglass_data.get('top_long_short_ratio', 'N/A')}
- 净持仓累积：{coinglass_data.get('net_position_cum', 'N/A')}
- 订单簿失衡率：{coinglass_data.get('orderbook_imbalance', 0.0):.2f}

**资金流向**
- CVD信号：{coinglass_data.get('cvd_signal', 'N/A')}
- **交易所钱包余额**：{bal_text}

**期权与宏观**
- 期权最大痛点：{option_pain} USDT
- 恐惧贪婪指数：{fg.get('value', '50')} (前值：{fg.get('prev', '50')})
- **ETH/BTC汇率趋势**：{eth_btc_trend}（当前汇率 {eth_btc_ratio:.6f}）
- **宏观三因子信号**：
{macro_signals_text}

### 📁 原始数据视图

**清算强度分布（价格 → 原始强度 → 距现价 ATR）**
| 价格 | 作用 | 原始强度 | 距现价(ATR) |
|------|------|----------|-------------|
{liq_profile_table}

**🔥 前三强清算区（按强度排序）**
{top_3_summary}

> ⚠️ 注：原始强度值为 CoinGlass 提供的相对量纲，数值越大代表清算压力越集中，并非美元名义金额。

**CVD 序列（单位：千美元）**
`{cvd_series_str}`

**多空账户人数比序列**
`{ls_series_str}`

**主动买卖比率序列**
`{taker_series_str}`

---

### 🔬 强制指标逐项分析任务

【强制执行：十项指标独立审查与交叉质证】
请严格按照以下编号顺序，对每一项指标执行**先读数、再对比、后定性**的分析流程。
**数据缺失时必须明确注明“数据缺失，无法研判”，严禁强行解读。**

### 🔍 1. 清算不对称性评估
- **读取数值**：上方/下方累计清算额比值为 `[填入具体数值]`。
- **阈值判断**：是否触发 ≥2.0（严重偏空） 或 ≤0.5（严重偏多）？
- **⚠️ 例外条款（必须执行）**：若当前价格位于 **15分钟 EMA 60 上方且该均线斜率向上**，即使比值 ≥3.0 也不得判空，应解读为“多头清算燃料充沛”；反之若价格位于均线下方且斜率向下，即使比值 ≤0.33 也不得判多。
- **点位量化**：最强三档价格距离当前价的**精确ATR倍数**分别是多少？
- **独立结论**：[在此仅基于该数值给出单指标倾向]。

### 🔍 2. CVD（累积成交量Delta）趋势与背离诊断
- **序列解构**：序列整体趋势是[上升/下降]，**前后半段斜率变化为[前半段值] vs [后半段值]**。
- **背离检测**：当前价格创[新低/新高]的同时，CVD是否未同步创[新低/新高]？（是/否）。
- **独立结论**：[动能衰竭/动能加速]。

### 🔍 3. 持仓结构矛盾识别
- **数值对比**：顶级交易员多空比 `[数值]`，而净持仓累积方向为 `[累积值正/负]`。
- **噪声过滤**：若持仓量24h变化绝对值 < 2%，则此矛盾降级为“数据噪声”，权重归零。
- **矛盾判定**：两者指向**一致**还是**冲突**？
- **独立结论**：[若冲突，指出“散户拥挤”或“大户诱盘”；若一致，指出“趋势共振”；若噪声则标注“忽略”]。

### 🔍 4. 清算信号验证与修正
- **系统标签**：系统给出信号为 `[系统信号值]`。
- **分布表对照**：该信号在清算分布表中**存在明确对应点位堆积**吗？（是/否）。
- **裁决修正**：若不存在，**必须推翻标签**，修正为“虚警信号”。

### 🔍 5. 宏观因子边际变化
- **精确读数**：恐惧贪婪指数昨值 `[X]` 今值 `[Y]`，**变化绝对值 `[|Y-X|]`**。
- **资金门槛**：稳定币市值7日变化率 `[具体百分比%]`，是否超过 **±1% 有效阈值**？
- **独立结论**：[情绪/资金面偏暖/偏冷]。

### 🔍 6. 主动买卖比率持续性
- **即时读数**：当前主动买卖比为 `[数值]`，主动方向为[买/卖]。
- **序列持续性**：过去 `[N]` 根K线中该方向连续性如何？（例如：过去6根有5根主动卖）。
- **独立结论**：[主动资金坚决/犹豫]。

### 🔍 7. 订单簿失衡率与深度验证
- **当前数值**：失衡率 `[数值]`，深度偏向[买盘墙/卖盘墙]。
- **同向验证**：是否与**指标6（主动买卖）** 的方向**逻辑一致**？（例如：都是卖压/都是买盘/背离）。
- **独立结论**：[实压/虚晃]。

### 🔍 8. ETH/BTC 汇率传导效应
- **汇率趋势**：当前ETH/BTC处于[上升/下降/盘整]通道。
- **作用域限定（强制）**：若交易标的为 `{symbol}` 且是 **BTC 本位合约**，此项结论强制降权至参考级，仅做背景描述；若为 **ETH 或山寨币合约**，则升权至关键级。
- **传导逻辑**：对 `{symbol}` 是构成**同向推动**还是**避险抽血**？
- **独立结论**：[利多/利空/中性，并注明是否因作用域降权]。

### 🔍 9. 交易所钱包余额流向（资金面硬指标）
- **净流向**：BTC净流出/流入 `[数量]`，稳定币净流出/流入 `[数量]`。
- **资金面定性**：根据“币流+稳定币流”组合，当前处于[积累/派发/观望]阶段。

### 🔍 10. 强制反方质证（推翻系统结论尝试）
- **攻击角度**：请刻意寻找**1至2个与系统建议 `[{higher_direction}]` 完全相反的**数据细节（例如：上方某处有大额挂单、费率已过热、CVD明显背离）。
- **质证结论**：
    - 若找到有力反证 -> 写明“**存在推翻依据：[具体反证内容]**”。
    - 若完全找不到 -> 写明“**无法推翻，因[某关键支撑/压力数据]被彻底突破/确认**”。

【最终裁决输出格式】
在完成上述10项分析后，请单独起一行，使用以下**精确格式**输出最终交易决策（此为数学验证闭环）：

`【最终裁决】推翻/一致系统建议 [做多/做空] → **执行 [做多/做空/观望]**  
- **核心依据**：因[关键指标1]读数[具体值]与[关键指标2]形成[共振/背离]。  
- **博弈边界**：确认站上/跌破 [具体价位 USDT] 前，当前多空结构不可证伪；若反向突破 [具体价位 USDT] 则逻辑失效。`

{quant_reference_section}

### 🎯 入场、止损与止盈设置

**你拥有完全的自主权**：请根据你的专业判断，独立设定入场区间、止损价、止盈价。无需参考任何预设公式。

---

### 策略输出格式（严格JSON）

**请直接输出纯 JSON，不要用 ```json 代码块包裹。**

{{
  "direction": "long" 或 "short" 或 "neutral",
  "confidence": "high" 或 "medium" 或 "low",
  "entry_price_low": 入场区间下限,
  "entry_price_high": 入场区间上限,
  "stop_loss": 止损价,
  "take_profit": 止盈价,
  "tp_anchor": "止盈设置理由",
  "analysis_summary": "按强制指标逐项分析任务逐项撰写简要核心结论，每条以 🔍 开头，共10项，末尾包含【最终裁决】段落。",
  "trader_commentary": "顶级交易员观点",
  "risk_note": "风险提示"
}}
"""
    return prompt
def call_deepseek(prompt: str, max_retries: int = 3) -> dict:
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1", timeout=120.0)
    for attempt in range(max_retries):
        try:
            logger.info(f"DeepSeek Reasoner API 调用 (尝试 {attempt+1}/{max_retries})，Prompt 长度: {len(prompt)} 字符")
            resp = client.chat.completions.create(
                model="deepseek-reasoner",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000
            )
            content = resp.choices[0].message.content
            logger.info(f"DeepSeek Reasoner 响应成功，原始内容长度: {len(content)}")

            json_str = None
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                if end != -1:
                    json_str = content[start:end].strip()
            if not json_str:
                start = content.find('{')
                end = content.rfind('}') + 1
                if start != -1 and end > start:
                    json_str = content[start:end]
            if not json_str:
                logger.warning(f"DeepSeek Reasoner 返回无有效 JSON，原始内容前200字符: {content[:200]}")
                if attempt == max_retries - 1:
                    raise ValueError("无法提取 JSON")
                continue

            s = json.loads(json_str)
            s.setdefault("tp_anchor", "未提供")
            s.setdefault("analysis_summary", "无分析摘要")
            s.setdefault("trader_reasoning", "")
            s.setdefault("risk_note", "")
            return s
        except Exception as e:
            logger.warning(f"DeepSeek Reasoner 调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                raise
    return {}


def validate_strategy(s: dict, price: float, atr: float = None) -> bool:
    """仅做最基本的方向和正数校验，完全信任 AI 的止损止盈设置"""
    direction = s.get("direction")
    if direction not in ["long", "short", "neutral"]:
        return False
    if direction == "neutral":
        return True
    try:
        entry_low = float(s.get("entry_price_low", 0))
        entry_high = float(s.get("entry_price_high", 0))
        stop = float(s.get("stop_loss", 0))
        tp = float(s.get("take_profit", 0))
        if entry_low <= 0 or entry_high <= 0 or stop <= 0 or tp <= 0:
            return False
        if entry_low > entry_high:
            return False
    except:
        return False
    return True
