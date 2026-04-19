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

    if entry_candidates is None:
        entry_candidates = {
            "rule1": {"low": 0.0, "high": 0.0, "anchor": "无"},
            "rule2": {"low": 0.0, "high": 0.0, "anchor": "无"},
            "rule3": {"low": 0.0, "high": 0.0, "anchor": "无"}
        }

    if tp_candidates is None:
        tp_candidates = {
            "rule1": {"price": 0.0, "anchor": "无"},
            "rule2": {"price": 0.0, "anchor": "无"},
            "rule3": {"price": 0.0, "anchor": "2:1盈亏比公式"}
        }

    eth_btc = coinglass_data.get("eth_btc_ratio", {})
    eth_btc_trend = eth_btc.get('trend', 'N/A')
    eth_btc_ratio = eth_btc.get('current_ratio', 0.0)

    raw_view = coinglass_data.get("raw_view", {})

    # 清算分布表
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
    cvd_series_str = str(cvd_series) if cvd_valid else "数据无效（该币种无CVD数据，跳过分析）"

    ls_valid = raw_view.get("ls_valid", False)
    ls_series = raw_view.get("ls_ratio_series_1h", [])
    ls_series_str = str(ls_series) if ls_valid else "数据无效（全为0）"

    taker_series = raw_view.get("taker_ratio_series_1h", [])
    taker_series_str = str(taker_series) if taker_series else "无数据"

    return f"""你是一位精通**清算动力学、多空博弈和数据量化分析**的顶尖加密货币短线合约交易员。你必须基于提供的原始数据，对**每一项指标**进行独立的、深度的专业研判。

⚠️ **核心要求**：
- 你必须**亲自分析每一项原始数据**，而非依赖系统给出的定性标签。
- 你的分析必须包含**具体数值引用**和**对比判断**。
- 你拥有最终裁决权，可以质疑系统建议，但必须在分析中给出明确理由。

{warning_text}{data_source_text}{extreme_liq_text}{trend_desc}

### 核心市场数据（你必须逐项分析）

**价格与波动**
- 当前价格：{price} USDT
- 4小时ATR：{atr} USDT
- 波动因子：{volatility_factor:.2f}（>1.3高波，<0.7低波）

**清算压力**
- 上方空头清算总额：{coinglass_data.get('above_short_liquidation', 'N/A')} USD
- 下方多头清算总额：{coinglass_data.get('below_long_liquidation', 'N/A')} USD
- 清算最大痛点：{liq_max_pain} USDT
- 最近清算密集区：{cluster.get('direction', 'N/A')}方 {cluster.get('price', 'N/A')} USDT，强度{cluster.get('intensity', 'N/A')}/5
- **清算动态信号**：{liq_dynamic_text}

**多空博弈**
- 资金费率：{coinglass_data.get('funding_rate', 'N/A')}%（>0.05%多头拥挤，<-0.03%空头拥挤）
- 持仓量24h变化：{coinglass_data.get('oi_change_24h', 'N/A')}%
- 主动吃单比率：{coinglass_data.get('taker_ratio', 'N/A')}（>0.55买盘主动，<0.45卖盘主动）
- 顶级交易员多空比：{coinglass_data.get('top_long_short_ratio', 'N/A')}（<0.7偏多，>2.0偏空）
- 净持仓累积：{coinglass_data.get('net_position_cum', 'N/A')}（>0主力累积多头，<0主力累积空头）
- 订单簿失衡率：{coinglass_data.get('orderbook_imbalance', 0.0):.2f}（>0.2买盘占优，<-0.2卖盘占优）

**资金流向**
- CVD信号：{coinglass_data.get('cvd_signal', 'N/A')}
- **交易所钱包余额**：{bal_text}

**期权与宏观**
- 期权最大痛点：{option_pain} USDT
- 恐惧贪婪指数：{fg.get('value', '50')} (前值：{fg.get('prev', '50')})
- **ETH/BTC汇率趋势**：{eth_btc_trend}（当前汇率 {eth_btc_ratio:.6f}）
- **宏观三因子信号**：
{macro_signals_text}

**量化参考（供辅助决策）**
- 方向倾向得分：多头 {bull_score} vs 空头 {bear_score}。当前{higher_direction}领先{diff}分。
- 系统信号评级参考：{signal_grade}（A=共振强烈，B=标准跟随，C=试探信号）

### 📁 原始数据视图（你必须深入分析）

**清算强度分布（价格 → 原始强度 → 距现价 ATR）**
| 价格 | 作用 | 原始强度 | 距现价(ATR) |
|------|------|----------|-------------|
{liq_profile_table}

**🔥 前三强清算区（按强度排序）**
{top_3_summary}

> ⚠️ 注：原始强度值为 CoinGlass 提供的相对量纲，数值越大代表清算压力越集中，并非美元名义金额。

**CVD 序列（最近 60 分钟，1 分钟粒度，单位：千美元）**
`{cvd_series_str}`

**多空账户人数比（最近 6 小时，1 小时间隔）**
`{ls_series_str}`

**主动买卖比率（最近 6 小时，1 小时间隔）**
`{taker_series_str}`

---

### 🔬 强制指标逐项分析任务（你必须完成，每项均需分析，不得遗漏）

**首先，评估数据质量**（在第一条观察中声明）：
- CVD 序列是否有效？若无效，后续 CVD 分析跳过并注明。
- 多空比序列是否全为 0？若是，后续多空比分析跳过并注明。
- 系统清算动态信号是否能在分布表中找到对应价格？若找不到，以分布表为准。

**然后，你必须逐项分析以下指标**（每条以 🔍 开头，必须包含具体数值和判断）：

1. **清算不对称的精确量化**  
   - 计算：上方空头清算总额 ÷ 下方多头清算总额 = ？  
   - 判断：该比值是否 ≥ 2.0（显著偏空）或 ≤ 0.5（显著偏多）？  
   - 定位：在清算分布表中，找出**强度最高的 3 个价格档位**，并指出它们距当前价的 ATR 倍数。

2. **CVD 序列的趋势与背离分析**（若数据无效则注明“数据无效，跳过”）  
   - 观察序列整体趋势，判断买方/卖方主导程度。  
   - 检查变化率，判断动能是加速、衰减还是持续。  
   - **核心任务**：对比 CVD 趋势与价格走势，判断是否存在背离。

3. **持仓结构的矛盾挖掘**（若多空比序列无效，则注明并跳过）  
   - 对比多空账户人数比的最新值与 6 小时前的变化方向。  
   - 对比顶级交易员多空比与净持仓累积的方向一致性。

4. **清算动态信号的验证**  
   - 系统信号是否在分布表中存在？若不存在，以分布表为准重新评估关键位。

5. **宏观因子的边际变化**  
   - 恐惧贪婪指数较昨日变化了多少？  
   - 稳定币市值 7 日变化率是否超过 ±1%？若数据缺失则注明。

6. **主动买卖比率（Taker Ratio）分析**  
   - 当前值是多少？属于买盘主动（>0.55）、卖盘主动（<0.45）还是均衡？  
   - 结合 6 小时序列，判断主动买卖方向是否具有持续性。

7. **订单簿失衡率分析**  
   - 当前值是多少？买盘深度占优（>0.2）、卖盘深度占优（<-0.2）还是均衡？  
   - 该指标与主动买卖比率是否同向？若背离，说明什么？

8. **ETH/BTC 汇率趋势分析**  
   - 当前趋势是 up 还是 down？  
   - 该趋势对 {symbol} 的方向是强化还是削弱？

9. **交易所钱包余额流向分析**  
   - BTC 是净流入还是净流出？稳定币是净流入还是净流出？  
   - 结合两者判断中长期资金面是偏多还是偏空。

**最终裁决要求**：
在所有观察之后，你必须用单独一行写一个【最终裁决】段落，格式为：
`【最终裁决】综合以上分析，我决定输出 [long/short/neutral]，理由：...`

**输出要求**：以上 9 项观察必须全部整合进 `analysis_summary` 字段中，每条以 🔍 开头，不得遗漏任何一项。

---

### ⚖️ 裁决指引（你拥有最终决定权）

系统建议：若清算结构偏多且分差 ≥ {threshold_bull_bear}，建议 `long`；若偏空且分差 ≥ {threshold_bull_bear}，建议 `short`。

**你的权力**：你必须独自裁决，系统建议只做参考，必须在 `analysis_summary` 中给出明确理由。

---

### 🎯 入场、止损与止盈设置

**入场区间候选**：
- 规则1（清算区）：{entry_candidates['rule1']['low']:.1f} - {entry_candidates['rule1']['high']:.1f}
- 规则2（关键位）：{entry_candidates['rule2']['low']:.1f} - {entry_candidates['rule2']['high']:.1f}
- 规则3（ATR追单）：{entry_candidates['rule3']['low']:.1f} - {entry_candidates['rule3']['high']:.1f}

**止盈候选**：
- 候选A：{tp_candidates['rule1']['price']:.1f}（{tp_candidates['rule1']['anchor']}）
- 候选B：{tp_candidates['rule2']['price']:.1f}（{tp_candidates['rule2']['anchor']}）
- 候选C：{tp_candidates['rule3']['price']:.1f}（{tp_candidates['rule3']['anchor']}）

你可在候选C的 ±0.3×ATR 内微调。

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
  "tp_anchor": "止盈锚定来源说明",
  "analysis_summary": "按强制指标逐项分析任务逐项撰写，每条以 🔍 开头，共9项，末尾包含【最终裁决】段落。",
  "trader_commentary": "顶级交易员观点",
  "risk_note": "风险提示"
}}
"""


def call_deepseek(prompt: str, max_retries: int = 3) -> dict:
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1", timeout=60.0)
    for attempt in range(max_retries):
        try:
            logger.info(f"DeepSeek API 调用 (尝试 {attempt+1}/{max_retries})，Prompt 长度: {len(prompt)} 字符")
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2500  # 增加 token 数以容纳 9 项分析
            )
            content = resp.choices[0].message.content
            logger.info(f"DeepSeek 响应状态: 成功，原始内容长度: {len(content)}")

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
                logger.warning(f"DeepSeek 返回无有效 JSON，原始内容前200字符: {content[:200]}")
                if attempt == max_retries - 1:
                    raise ValueError("无法提取 JSON")
                continue

            s = json.loads(json_str)
            s.setdefault("tp_anchor", "未提供")
            s.setdefault("analysis_summary", "无分析摘要")
            s.setdefault("trader_commentary", "")
            return s
        except Exception as e:
            logger.warning(f"DeepSeek调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                raise
    return {}


def validate_strategy(s: dict, price: float, atr: float = None) -> bool:
    if s.get("direction") not in ["long", "short", "neutral"]: return False
    if s["direction"] == "neutral": return True
    try:
        entry_low = float(s.get("entry_price_low", 0))
        entry_high = float(s.get("entry_price_high", 0))
        stop = float(s.get("stop_loss", 0))
        tolerance = 0.5 * (atr if atr else price * 0.02)
        if s["direction"] == "long" and stop >= entry_low - tolerance: return False
        if s["direction"] == "short" and stop <= entry_high + tolerance: return False
    except: return False
    return True