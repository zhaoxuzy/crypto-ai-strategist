# 在调用 build_prompt 前添加

# 1. 方向倾向得分（沿用原有 compute_directional_scores）
directional_scores = compute_directional_scores(symbol, cg_data, macro, trend_info)

# 2. 信号评级参考（基于三支柱简单加权，或复用 signal_strength 的等级映射）
#    此处示例使用 signal_strength 的 level 映射为 A/B/C
signal_strength = calculate_signal_strength(
    symbol, "long", cg_data, macro, liq_zero_count,
    cg.get_eth_btc_ratio(), cg.get_exchange_balances(), trend_info, extreme_liq
)
score = signal_strength["score"]
if score >= 65:
    signal_grade = "A"
elif score >= 40:
    signal_grade = "B"
else:
    signal_grade = "C"

# 3. 止损候选值（规则1：同向清算区外侧；规则2：1.5ATR；规则3：2ATR）
stop_candidates = {
    "rule1": 0.0,
    "rule2": price - 1.5 * atr if trend_info.get("direction") == "bull" else price + 1.5 * atr,
    "rule3": price - 2.0 * atr if trend_info.get("direction") == "bull" else price + 2.0 * atr
}
# 计算规则1（需判断同向清算区）
cluster = cg_data.get("nearest_cluster", {})
cluster_dir = cluster.get("direction", "")
cluster_price = float(cluster.get("price", 0)) if cluster.get("price", "N/A") != "N/A" else 0
if cluster_intensity >= 3 and cluster_price > 0:
    if trend_info.get("direction") == "bull" and cluster_dir == "下":
        stop_candidates["rule1"] = cluster_price * 0.998
    elif trend_info.get("direction") == "bear" and cluster_dir == "上":
        stop_candidates["rule1"] = cluster_price * 1.002
if stop_candidates["rule1"] == 0.0:
    stop_candidates["rule1"] = stop_candidates["rule2"]  # 无同向清算区则回退

# 4. 止盈候选值（TP1：反向清算区；TP2：最大痛点或下一个清算簇）
tp_candidates = {"tp1": 0.0, "tp1_anchor": "未提供", "tp2": 0.0, "tp2_anchor": "未提供"}
# TP1：反向清算区（做多时看上方的空头清算区）
if trend_info.get("direction") == "bull":
    # 可从 coinglass_data 中解析上方最近强度≥3的清算区
    tp_candidates["tp1"] = price + 2.0 * atr  # 占位，实际需解析
else:
    tp_candidates["tp1"] = price - 2.0 * atr
# TP2：清算最大痛点
max_pain = float(cg_data.get("max_pain_price", 0)) if cg_data.get("max_pain_price", "N/A") != "N/A" else 0
if max_pain > 0:
    tp_candidates["tp2"] = max_pain
    tp_candidates["tp2_anchor"] = "清算最大痛点"
else:
    tp_candidates["tp2"] = tp_candidates["tp1"] * 1.5 if trend_info.get("direction") == "bull" else tp_candidates["tp1"] * 0.5

# 传入 build_prompt
prompt = build_prompt(
    symbol=symbol, price=price, atr=atr, coinglass_data=cg_data, macro_data=macro,
    profile=profile, volatility_factor=volatility_factor, trend_info=trend_info,
    extreme_liq=extreme_liq, liq_warning=liq_warning, data_source_status=data_source_status,
    directional_scores=directional_scores,
    signal_grade=signal_grade,
    stop_candidates=stop_candidates,
    tp_candidates=tp_candidates
)
