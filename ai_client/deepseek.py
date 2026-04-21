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

    # === 修改：双向评分表 ===
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
- 你必须专业分析，你的策略因避免30分钟或180分钟后就失效，就变成了相反的方向。

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

**【数据使用原则】**
系统定性标签仅供参考。分析时必须优先信任原始序列。若标签与序列趋势不符，以序列为准。

**逐项分析（共10项，每条以 🔍 开头，引用具体数值，给出核心结论）**：
1. 清算不对称：上方/下方比值=？是否≥2或≤0.5？最强三档价格及ATR倍数。
2. CVD趋势与背离：序列整体趋势，前后半段变化，动能状态，与价格是否背离。
3. 持仓结构矛盾：顶级多空比与净持仓累积方向是否一致。
4. 清算信号验证：系统信号在分布表中存在否？以分布表为准修正。
5. 宏观因子边际变化：恐惧贪婪较昨日变化，稳定币7日变化率超±1%否。
6. 主动买卖比率：当前值及买卖主动方向，结合序列判断持续性。
7. 订单簿失衡率：当前值及深度偏向，与主动买卖是否同向。
8. ETH/BTC汇率趋势：趋势方向及对{symbol}的影响。
9. 交易所钱包余额流向：BTC与稳定币净流向，资金面偏多/偏空。
10. 尝试推翻系统结论：故意找出反驳系统建议({higher_direction})的理由；若完全认同，解释为何无反驳证据。

**最终裁决要求**：
综合分析研判以上数据后，用单独一行写【最终裁决】段落：
`【最终裁决】系统建议 [{higher_direction}]，我以一个顶级交易员的角色分析后决定输出 [做多/做空/观望]（若与系统一致，写“一致”；若相反，写“推翻”）。核心依据：...`

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
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1", timeout=90.0)
    for attempt in range(max_retries):
        try:
            logger.info(f"DeepSeek API 调用 (尝试 {attempt+1}/{max_retries})，Prompt 长度: {len(prompt)} 字符")
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2500
            )
            content = resp.choices[0].message.content
            logger.info(f"DeepSeek 响应成功，原始内容长度: {len(content)}")

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
            logger.warning(f"DeepSeek 调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                raise
    return {}


def validate_strategy(s: dict, price: float, atr: float = None) -> tuple[bool, str]:
    """仅做最基本的方向和正数校验，完全信任 AI 的止损止盈设置"""
    direction = s.get("direction")
    if direction not in ["long", "short", "neutral"]:
        return False, f"无效方向: {direction}"
    if direction == "neutral":
        return True, ""
    try:
        entry_low = float(s.get("entry_price_low", 0))
        entry_high = float(s.get("entry_price_high", 0))
        stop = float(s.get("stop_loss", 0))
        tp = float(s.get("take_profit", 0))
        if entry_low <= 0 or entry_high <= 0 or stop <= 0 or tp <= 0:
            return False, "入场/止损/止盈必须为正数"
        if entry_low > entry_high:
            return False, "入场区间下限大于上限"
    except Exception as e:
        return False, f"数值解析失败: {e}"
    return True, ""
