import os
import json
import re
from openai import OpenAI
from utils.logger import logger

# ==================== 原有辅助函数（保持不变） ====================
def linear_score(v: float, low: float, high: float, full: float, rev: bool = False) -> float:
    if low == high:
        return 0.0
    if rev:
        return full if v <= low else (0.0 if v >= high else full * (high - v) / (high - low))
    else:
        return 0.0 if v <= low else (full if v >= high else full * (v - low) / (high - low))


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
    except:
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
    except:
        pass
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
        raw = linear_score(ratio, 0.2, 0.5, weight_liq, True) if direction == "long" else linear_score(ratio, 0.5, 0.8, weight_liq, False)
        s = raw * min(1.0, total / LIQ_MIN.get(symbol.upper(), 50_000_000))
        score += s
        if s > 5:
            det.append(f"清算结构({ratio:.1%})")

    pos_s, pos_d = get_position_structure_score(direction, cg, macro, symbol)
    score += pos_s * (weight_pos / 32.0)
    det.extend(pos_d)

    cvd = cg.get("cvd_signal", "N/A")
    if cvd in ["bullish", "slightly_bullish"]:
        if direction == "long":
            score += weight_cvd if cvd == "bullish" else weight_cvd * 0.7
            det.append(f"CVD:{cvd}")
        else:
            score -= weight_cvd * 0.5
            det.append("CVD反向")
    elif cvd in ["bearish", "slightly_bearish"]:
        if direction == "short":
            score += weight_cvd if cvd == "bearish" else weight_cvd * 0.7
            det.append(f"CVD:{cvd}")
        else:
            score -= weight_cvd * 0.5
            det.append("CVD反向")

    fg_val = int(macro.get("fear_greed", {}).get("value", 50))
    s = linear_score(fg_val, 20, 50, weight_fg, True) if direction == "long" else linear_score(fg_val, 50, 80, weight_fg, False)
    score += s
    if s > 2:
        det.append(f"恐惧贪婪({fg_val})")

    try:
        fr = float(cg.get("funding_rate", 0))
        s = linear_score(fr, 0.02, 0.08, weight_fr, False) if direction == "short" else linear_score(fr, -0.08, -0.01, weight_fr, True)
        score += s
        if abs(s) > 1:
            det.append(f"费率({fr:.4f})")
    except:
        pass

    try:
        tr = float(cg.get("taker_ratio", 0.5))
        s = linear_score(tr, 0.5, 0.65, weight_taker, False) if direction == "long" else linear_score(tr, 0.35, 0.5, weight_taker, True)
        score += s
        if s > 2:
            det.append(f"主动买卖({tr:.2f})")
    except:
        pass

    try:
        np = float(cg.get("net_position_cum", 0))
        oi = float(cg.get("option_oi_usd", 1)) if cg.get("option_oi_usd", "N/A") != "N/A" else 1.0
        pct = (np / oi * 100) if oi > 0 else 0.0
        s = linear_score(pct, 1.0, 3.0, weight_net, False) if direction == "long" else linear_score(pct, -3.0, -1.0, weight_net, True)
        score += s
        if abs(s) > 1:
            det.append(f"净持仓({pct:.1f}%)")
    except:
        pass

    imb = cg.get("orderbook_imbalance", 0.0)
    s = linear_score(imb, 0.1, 0.3, weight_ob, False) if direction == "long" else linear_score(imb, -0.3, -0.1, weight_ob, True)
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
        btc_flow, stable_flow = bal.get("btc_flow", "neutral"), bal.get("stable_flow", "neutral")
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
    # 该函数与您原有 build_prompt 完全一致，此处省略以节省篇幅
    # 实际使用时请将原函数完整粘贴在此处
    pass


# ==================== 新增：审计与验证函数 ====================
def audit_analysis_summary(analysis_text: str, coinglass_data: dict, raw_view: dict) -> dict:
    """
    从模型生成的 analysis_summary 中提取审计追踪信息，并与后端计算结果比对。
    返回字典包含 passed 标志和 discrepancies 列表。
    """
    audit = {}
    match = re.search(r"__AUDIT_TRAIL__\s*(.*?)(?:\n|$)", analysis_text)
    if match:
        trail_str = match.group(1).strip()
        for part in trail_str.split(','):
            if '=' in part:
                k, v = part.split('=', 1)
                audit[k.strip()] = v.strip()

    # 后端计算基准值
    above = float(str(coinglass_data.get("above_short_liquidation", "0")).replace(",", ""))
    below = float(str(coinglass_data.get("below_long_liquidation", "0")).replace(",", ""))
    actual_liq_ratio = above / below if below > 0 else 0.0

    liq_profile = raw_view.get("liquidation_profile", [])
    top_zone_price = None
    if liq_profile:
        sorted_zones = sorted(liq_profile, key=lambda x: x.get("intensity", 0), reverse=True)
        top_zone_price = sorted_zones[0]["price"] if sorted_zones else None

    cvd_series = raw_view.get("cvd_series_1m", [])
    cvd_trend = "INVALID"
    if cvd_series and raw_view.get("cvd_valid", False):
        if all(v >= 0 for v in cvd_series):
            cvd_trend = "UP"
        elif all(v <= 0 for v in cvd_series):
            cvd_trend = "DOWN"
        else:
            cvd_trend = "NEUTRAL"

    ls_series = raw_view.get("ls_ratio_series_1h", [])
    lsr_invalid = all(v == 0 for v in ls_series) if ls_series else True

    discrepancies = []
    passed = True

    model_ratio = float(audit.get("liq_ratio", 0))
    if abs(model_ratio - actual_liq_ratio) > 0.05 * actual_liq_ratio and actual_liq_ratio > 0:
        discrepancies.append(f"清算比值不符：模型计算{model_ratio:.3f}，实际{actual_liq_ratio:.3f}")
        passed = False

    model_top_price = float(audit.get("top_zone_price", 0)) if audit.get("top_zone_price") else None
    if top_zone_price is not None and model_top_price is not None:
        if abs(model_top_price - top_zone_price) > 1.0:
            discrepancies.append(f"最高清算价格不符：模型{model_top_price:.2f}，实际{top_zone_price:.2f}")
            passed = False

    model_cvd = audit.get("cvd_trend", "").upper()
    if model_cvd and model_cvd != cvd_trend:
        discrepancies.append(f"CVD趋势判断不符：模型{model_cvd}，实际{cvd_trend}")
        passed = False

    model_lsr = audit.get("lsr_invalid", "").upper()
    expected_lsr = "TRUE" if lsr_invalid else "FALSE"
    if model_lsr and model_lsr != expected_lsr:
        discrepancies.append(f"多空比无效性判断不符：模型{model_lsr}，实际{expected_lsr}")
        passed = False

    return {"passed": passed, "discrepancies": discrepancies, "audit_data": audit}


def evaluate_neutral_penalty(result: dict, data_quality: dict) -> float:
    """对 neutral 决策计算信号权重折扣"""
    if result.get("direction") != "neutral":
        return 1.0
    cvd_valid = data_quality.get("cvd_valid", False)
    lsr_valid = data_quality.get("lsr_valid", False)
    if cvd_valid and lsr_valid:
        return 0.3
    return 0.8


# ==================== 两阶段 Prompt 构建 ====================
def build_analysis_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict,
                          profile: dict, volatility_factor: float = 1.0, trend_info: dict = None,
                          extreme_liq: bool = False, liq_warning: str = "", data_source_status: str = "",
                          directional_scores: dict = None, signal_grade: str = "B",
                          entry_candidates: dict = None, exchange_balances: dict = None,
                          liq_dynamic_signals: list = None,
                          threshold_bull_bear: int = 8, threshold_warning: int = 12,
                          tp_candidates: dict = None) -> str:
    """
    第一阶段：生成深度分析文本，末尾包含审计追踪。
    基于原有 build_prompt，但去除 JSON 输出部分，并增加审计要求。
    """
    # 直接调用原有 build_prompt 获取大部分内容，然后稍作修改
    base_prompt = build_prompt(
        symbol, price, atr, coinglass_data, macro_data, profile, volatility_factor,
        trend_info, extreme_liq, liq_warning, data_source_status, directional_scores,
        signal_grade, entry_candidates, exchange_balances, liq_dynamic_signals,
        threshold_bull_bear, threshold_warning, tp_candidates
    )

    # 移除原有的 JSON 输出部分，并添加审计任务
    # 查找 "### 策略输出格式（严格JSON）" 并截断
    json_start = base_prompt.find("### 策略输出格式（严格JSON）")
    if json_start != -1:
        base_prompt = base_prompt[:json_start].strip()

    audit_task = """
### 🔐 内部审计要求（必须严格遵守）
在完成上述强制数据深潜任务后，请在你的分析末尾添加一行以 `__AUDIT_TRAIL__` 开头的内容，格式为：
`__AUDIT_TRAIL__ liq_ratio=上方空头清算总额÷下方多头清算总额(保留3位小数), top_zone_price=清算分布表中强度最高档位的价格(取第一行), cvd_trend=UP/DOWN/NEUTRAL/INVALID, lsr_invalid=TRUE/FALSE`

示例：`__AUDIT_TRAIL__ liq_ratio=2.351, top_zone_price=95200.00, cvd_trend=DOWN, lsr_invalid=FALSE`

请直接输出你的深度分析报告，每条以 🔍 开头，末尾必须包含 `__AUDIT_TRAIL__` 行。
"""
    return base_prompt + "\n" + audit_task


def build_decision_prompt(analysis_text: str, entry_candidates: dict, tp_candidates: dict,
                          threshold_bull_bear: int, directional_scores: dict) -> str:
    """
    第二阶段：基于分析文本生成纯 JSON 策略。
    """
    return f"""以下是你的深度分析报告：
---
{analysis_text}
---

基于以上分析，请输出一个严格的 JSON 对象，用于量化交易策略。要求：
- direction: "long" / "short" / "neutral"
- confidence: "high" / "medium" / "low"
- entry_price_low: 入场区间下限
- entry_price_high: 入场区间上限
- stop_loss: 止损价
- take_profit: 止盈价
- tp_anchor: 止盈锚定来源说明
- analysis_summary: 必须完整复制上面分析报告的全部内容（包括 🔍 条目和 __AUDIT_TRAIL__）
- trader_commentary: 交易员主观备注（可选）
- risk_note: 风险提示

⚠️ 严格规则：
1. 只有当分析中明确存在严重数据缺失或多空信号绝对矛盾时，才允许输出 neutral。
2. 止损价必须符合方向逻辑：做多时 stop_loss < entry_price_low，做空时 stop_loss > entry_price_high。
3. 请直接输出纯 JSON，不要用 ```json 代码块包裹。
"""


# ==================== 增强版 DeepSeek 调用 ====================
def call_deepseek_enhanced(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict,
                           profile: dict, volatility_factor: float = 1.0, trend_info: dict = None,
                           extreme_liq: bool = False, liq_warning: str = "", data_source_status: str = "",
                           directional_scores: dict = None, signal_grade: str = "B",
                           entry_candidates: dict = None, exchange_balances: dict = None,
                           liq_dynamic_signals: list = None,
                           threshold_bull_bear: int = 8, threshold_warning: int = 12,
                           tp_candidates: dict = None) -> dict:
    """
    两阶段调用，包含审计和 neutral 惩罚逻辑。
    """
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1", timeout=60.0)

    raw_view = coinglass_data.get("raw_view", {})

    # 第一阶段：分析
    analysis_prompt = build_analysis_prompt(
        symbol, price, atr, coinglass_data, macro_data, profile, volatility_factor,
        trend_info, extreme_liq, liq_warning, data_source_status, directional_scores,
        signal_grade, entry_candidates, exchange_balances, liq_dynamic_signals,
        threshold_bull_bear, threshold_warning, tp_candidates
    )

    logger.info("第一阶段：请求深度分析...")
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0.4,
                max_tokens=1800
            )
            analysis_text = resp.choices[0].message.content
            logger.info(f"分析文本长度: {len(analysis_text)}")
            break
        except Exception as e:
            logger.warning(f"分析阶段调用失败 (尝试 {attempt+1}/3): {e}")
            if attempt == 2:
                raise

    # 审计
    audit_result = audit_analysis_summary(analysis_text, coinglass_data, raw_view)
    logger.info(f"审计结果: {audit_result['passed']}, 差异: {audit_result['discrepancies']}")

    # 第二阶段：决策
    decision_prompt = build_decision_prompt(analysis_text, entry_candidates, tp_candidates,
                                            threshold_bull_bear, directional_scores)

    logger.info("第二阶段：生成结构化决策...")
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": decision_prompt}],
                temperature=0.1,
                max_tokens=800
            )
            content = resp.choices[0].message.content
            logger.info(f"决策响应长度: {len(content)}")
            break
        except Exception as e:
            logger.warning(f"决策阶段调用失败 (尝试 {attempt+1}/3): {e}")
            if attempt == 2:
                raise

    # 提取 JSON
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
        raise ValueError("无法从响应中提取有效 JSON")

    result = json.loads(json_str)
    result.setdefault("tp_anchor", "未提供")
    result.setdefault("analysis_summary", analysis_text)
    result.setdefault("trader_commentary", "")
    result.setdefault("risk_note", "")

    data_quality = {
        "cvd_valid": raw_view.get("cvd_valid", False),
        "lsr_valid": not all(v == 0 for v in raw_view.get("ls_ratio_series_1h", []))
    }
    signal_weight = evaluate_neutral_penalty(result, data_quality)
    result["audit_passed"] = audit_result["passed"]
    result["audit_discrepancies"] = audit_result["discrepancies"]
    result["signal_weight"] = signal_weight

    return result


# ==================== 增强版策略验证 ====================
def validate_strategy_enhanced(s: dict, price: float, atr: float = None) -> tuple:
    """
    返回 (is_valid, error_message)
    """
    if s.get("direction") not in ["long", "short", "neutral"]:
        return False, "方向无效"
    if s["direction"] == "neutral":
        return True, ""

    try:
        entry_low = float(s.get("entry_price_low", 0))
        entry_high = float(s.get("entry_price_high", 0))
        stop = float(s.get("stop_loss", 0))
        tp = float(s.get("take_profit", 0))
    except (TypeError, ValueError):
        return False, "价格字段非数字"

    tolerance = 0.5 * (atr if atr else price * 0.02)
    entry_mid = (entry_low + entry_high) / 2

    if s["direction"] == "long":
        if stop >= entry_low - tolerance:
            return False, f"做多止损({stop})应低于入场下限({entry_low})"
        if tp <= entry_high + tolerance:
            return False, f"做多止盈({tp})应高于入场上限({entry_high})"
    else:  # short
        if stop <= entry_high + tolerance:
            return False, f"做空止损({stop})应高于入场上限({entry_high})"
        if tp >= entry_low - tolerance:
            return False, f"做空止盈({tp})应低于入场下限({entry_low})"

    # 可选盈亏比提示
    risk = abs(entry_mid - stop)
    reward = abs(tp - entry_mid)
    if risk > 0 and reward / risk < 1.5:
        logger.warning(f"盈亏比偏低: {reward/risk:.2f} (建议≥1.5)")

    return True, ""


# ==================== 兼容旧接口（可选） ====================
def call_deepseek(prompt: str, max_retries: int = 3) -> dict:
    """
    原 call_deepseek 函数已弃用，请改用 call_deepseek_enhanced。
    """
    raise NotImplementedError("请使用 call_deepseek_enhanced 并传入结构化参数")


def validate_strategy(s: dict, price: float, atr: float = None) -> bool:
    """
    原 validate_strategy 函数，保持可用。
    """
    is_valid, _ = validate_strategy_enhanced(s, price, atr)
    return is_valid
