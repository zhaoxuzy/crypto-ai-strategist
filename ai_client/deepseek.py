def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict,
                 profile: dict, volatility_factor: float = 1.0, trend_info: dict = None,
                 extreme_liq: bool = False, liq_warning: str = "", data_source_status: str = "",
                 directional_scores: dict = None, signal_grade: str = "B",
                 entry_candidates: dict = None, exchange_balances: dict = None,
                 liq_dynamic_signals: list = None,
                 threshold_bull_bear: int = 8, threshold_warning: int = 12,
                 tp_candidates: dict = None,
                 # === 新增参数（向后兼容） ===
                 bull_score: int = 0, bear_score: int = 0,
                 bull_factors: str = "", bear_factors: str = "") -> str:
    fg = macro_data.get("fear_greed", {})
    cluster = coinglass_data.get("nearest_cluster", {})
    liq_max_pain = coinglass_data.get("max_pain_price", "N/A")
    option_pain = coinglass_data.get("skew", "N/A")
    warning_text = f"\n{liq_warning}\n" if liq_warning else ""
    data_source_text = f"\n**{data_source_status}**\n" if data_source_status else ""
    extreme_liq_text = ("\n⚠️ **极端清算警报**（系统判定：单侧清算额超过历史均值3倍）\n" if extreme_liq else "")

    # 优先使用新参数，若未传入则回退到 directional_scores
    if directional_scores:
        bull_score = directional_scores.get("bull", bull_score)
        bear_score = directional_scores.get("bear", bear_score)
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

    # 获取持仓量变化和净持仓累积，用于 OI-价格矩阵分析
    oi_change_str = coinglass_data.get('oi_change_24h', 'N/A')
    net_position_cum = coinglass_data.get('net_position_cum', 'N/A')

    quant_reference_section = f"""
### 📟 内部量化引擎输出（仅供参考，AI 必须重新验证）

⚠️ **警告：此分值为机器根据规则硬算得出，未经过上下文校验。你必须基于上方原始数据独立判断。**

| 方向 | 得分 | 主要加分项 | 主要减分项 |
|------|------|------------|------------|
| 多头 | {bull_score} | {bull_factors if bull_factors else '无'} | - |
| 空头 | {bear_score} | {bear_factors if bear_factors else '无'} | - |
当前机械评级：{signal_grade}。{higher_direction}领先{diff}分。
"""

    prompt = f"""你是一位精通**清算动力学、多空博弈和数据量化分析**的顶尖加密货币短线合约交易员。你必须基于提供的原始数据，对**每一项指标**进行独立的、深度的专业研判。

⚠️ **核心要求**：
- 你必须**亲自分析每一项原始数据**，而非依赖系统给出的定性标签。
- 你的分析必须包含**具体数值引用**和**对比判断**。
- 你拥有最终裁决权，可以质疑系统建议，但必须在分析中给出明确理由。

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
- 持仓量24h变化：{oi_change_str}%
- 主动吃单比率：{coinglass_data.get('taker_ratio', 'N/A')}
- 顶级交易员多空比：{coinglass_data.get('top_long_short_ratio', 'N/A')}
- 净持仓累积：{net_position_cum}
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

### 🔬 强制指标逐项分析任务（共10项，每条以 🔍 开头）

**【数据使用原则】**
系统定性标签仅供参考。分析时必须优先信任原始序列。若标签与序列趋势不符，以序列为准。

**逐项分析要求（每条必须包含具体数值引用和核心结论）**：

🔍 1. **清算不对称与磁吸陷阱判定**
   - 计算上方/下方清算额比值，判断是否≥2或≤0.5。
   - 列出最强三档清算区的价格及ATR倍数。
   - **⚠️ 强制陷阱验证**：对于距现价1.5倍ATR内的最强清算区，结合CVD序列判断其性质：
     * 若CVD序列近半段持续同向增长，则该清算区是**有效磁吸目标**；
     * 若CVD序列走平或反向，或价格已在该区域附近停滞超过2个4H周期，则该清算区更可能是**主力出货掩护/流动性陷阱**，不宜作为交易方向依据。

🔍 2. **CVD趋势与背离分析**
   - 观察CVD序列的整体趋势（上升/下降/震荡）。
   - 将序列分为前后两半段，比较动能变化（加速/减速/反转）。
   - **⚠️ 量价背离判定**：结合当前价格位置，判断CVD是否与价格产生背离（价格涨CVD跌、价格跌CVD涨）。若存在背离，必须明确指出现有趋势的可靠性存疑。

🔍 3. **持仓结构矛盾与OI-价格矩阵**
   - 顶级多空比与净持仓累积方向是否一致？
   - **⚠️ OI-价格矩阵判定（强制）**：根据持仓量24h变化与近期价格走势，填入以下矩阵得出结论：
     * 价格↑ + OI↑ → 新多入场，趋势健康，偏多
     * 价格↑ + OI↓ → 空头平仓推动，反弹质量差，谨慎
     * 价格↓ + OI↑ → 新空入场，趋势延续，偏空
     * 价格↓ + OI↓ → 多头平仓推动，恐慌末端，关注反转

🔍 4. **清算信号验证**
   - 系统给出的清算动态信号（{liq_dynamic_text}）在分布表中是否存在对应强度的价格区域？
   - 以分布表实际数据为准，修正或确认系统信号。

🔍 5. **宏观因子边际变化**
   - 恐惧贪婪指数较昨日变化（当前{fg.get('value', '50')}，前值{fg.get('prev', '50')}）。
   - 稳定币7日变化率（参考交易所钱包余额中稳定币净流向）。
   - **⚠️ 极端值反转法则**：恐惧贪婪低于25时，继续看空的风险收益比恶化，反转概率上升；高于75时，继续追多的风险收益比恶化。

🔍 6. **主动买卖比率动态**
   - 当前值及买卖主动方向。
   - 观察序列的持续性：是单边持续还是双向交替？
   - **⚠️ 吸收形态识别**：若主动买入比率高但对应时段价格滞涨或仅微涨，判定为**卖盘吸收**，倾向反转下跌；若主动卖出比率高但价格止跌，判定为**买盘吸收**，倾向反转上涨。

🔍 7. **订单簿失衡率**
   - 当前值及深度偏向（正=买方深度占优，负=卖方深度占优）。
   - 与主动买卖比率是否同向？若方向相反，说明挂单与吃单行为背离，市场存在博弈，方向信号需降权。

🔍 8. **ETH/BTC汇率趋势（2025年后新范式）**
   - 趋势方向（上升/下降/横盘）及当前汇率。
   - **⚠️ 强制约束**：自2025年起，ETH/BTC上升不再直接等同于山寨季或市场风险偏好上升。仅凭ETH/BTC单一指标不足以判断{symbol}的方向。若要用此指标支持观点，必须结合该币种自身的量价结构，否则此指标权重应大幅降低。

🔍 9. **交易所钱包余额流向**
   - BTC与稳定币的净流向组合。
   - 判定资金面偏多（稳定币流入+BTC流出）、偏空（稳定币流出+BTC流入）或中性。

🔍 10. **尝试推翻系统结论**
    - 故意找出至少一个反驳系统建议（{higher_direction}）的硬证据（引用具体数值）。
    - 若完全认同系统结论，需解释为何当前市场不存在有效反驳证据。

---

### ⚖️ 最终裁决

综合分析研判以上10项分析后，用单独一行输出【最终裁决】段落：

`【最终裁决】系统建议 [{higher_direction}]，我以一个顶级交易员的角色分析后决定输出 [做多/做空/观望]（若与系统一致，写“一致”；若相反，写“推翻”）。核心依据：...`

{quant_reference_section}

### 🎯 入场、止损与止盈设置

**你拥有完全的自主权**：请根据你的专业判断，独立设定入场区间、止损价、止盈价。无需参考任何预设公式。

**⚠️ 止损设定强制约束**：
- 止损幅度应结合价格附近的**支撑/阻力结构**（如清算密集区、前高前低）设定，而非机械使用4H ATR。
- 止损位**不得**与清算密集区重合（防止被定点猎杀）。
- 必须在分析摘要中简要说明止损设置的逻辑依据。

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
