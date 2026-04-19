# 准备数据
symbol = "BTC"
price = 97000.0
atr = 1500.0
coinglass_data = { ... }  # 包含 raw_view 字段
macro_data = { ... }
profile = { ... }
# ... 其他参数

# 调用增强版函数
result = call_deepseek_enhanced(
    symbol, price, atr, coinglass_data, macro_data, profile,
    volatility_factor=1.1,
    trend_info={"direction": "up", "score": 65, "confidence": "中", "signals": ["EMA金叉"]},
    extreme_liq=False,
    liq_warning="",
    data_source_status="数据源正常",
    directional_scores={"bull": 72, "bear": 48, "macro_signals": []},
    signal_grade="B",
    entry_candidates={
        "rule1": {"low": 96500, "high": 96800, "anchor": "清算区"},
        "rule2": {"low": 96200, "high": 96500, "anchor": "支撑位"},
        "rule3": {"low": 97000, "high": 97300, "anchor": "ATR突破"}
    },
    exchange_balances={"btc_flow": "out", "stable_flow": "in", "btc_change": -150, "stable_change": 200},
    liq_dynamic_signals=["上方97000累积空头清算"],
    threshold_bull_bear=8,
    threshold_warning=12,
    tp_candidates={
        "rule1": {"price": 99000, "anchor": "前高阻力"},
        "rule2": {"price": 98500, "anchor": "清算区"},
        "rule3": {"price": 98000, "anchor": "2:1盈亏比"}
    }
)

# 验证策略
is_valid, msg = validate_strategy_enhanced(result, price, atr)
if not is_valid:
    logger.error(f"策略验证失败: {msg}")

# 使用信号权重
final_weight = result.get("signal_weight", 1.0)
if result["audit_passed"]:
    logger.info(f"审计通过，信号方向: {result['direction']}，权重: {final_weight}")
else:
    logger.warning(f"审计未通过，差异: {result['audit_discrepancies']}，信号降级")
