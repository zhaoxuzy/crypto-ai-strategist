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
    extreme_liq_text = ("\n⚠️ 极端清算警报\n" if extreme_liq else "")

    bull_score = directional_scores.get("bull", 0) if directional_scores else 0
    bear_score = directional_scores.get("bear", 0) if directional_scores else 0
    diff = abs(bull_score - bear_score)
    higher_direction = "多头" if bull_score > bear_score else "空头"

    macro_signals = directional_scores.get("macro_signals", []) if directional_scores else []
    macro_signal_lines = [f"{s['text']}:{s['direction']}({s['weight']})" for s in macro_signals]
    macro_signals_text = ", ".join(macro_signal_lines) if macro_signal_lines else "无"

    liq_dynamic_text = "、".join(liq_dynamic_signals) if liq_dynamic_signals else "无"

    bal_text = ""
    if exchange_balances:
        btc_flow = exchange_balances.get("btc_flow", "neutral")
        stable_flow = exchange_balances.get("stable_flow", "neutral")
        bal_text = f"BTC:{exchange_balances.get('btc_change', 0):.0f}({btc_flow}) 稳定币:{exchange_balances.get('stable_change', 0):.0f}({stable_flow})"

    trend_desc = ""
    if trend_info:
        dir_t = trend_info.get('direction', 'neutral')
        score_t = trend_info.get('score', 0)
        trend_desc = f"趋势:{dir_t} {score_t}/100"

    eth_btc = coinglass_data.get("eth_btc_ratio", {})
    eth_btc_trend = eth_btc.get('trend', 'N/A')
    eth_btc_ratio = eth_btc.get('current_ratio', 0.0)

    raw_view = coinglass_data.get("raw_view", {})

    top_3_zones = raw_view.get("top_3_liquidation_zones", [])
    top_3_str = " | ".join([f"{z['price']:.2f}({z['effect']}强{z['intensity']:.1f}距{z['distance_atr']:+.2f}ATR)" for z in top_3_zones[:3]]) if top_3_zones else "无"

    cvd_valid = raw_view.get("cvd_valid", False)
    cvd_series = raw_view.get("cvd_series_1m", [])
    cvd_str = f"[{','.join(map(str, cvd_series))}]" if cvd_valid else "无效"

    ls_valid = raw_view.get("ls_valid", False)
    ls_series = raw_view.get("ls_ratio_series_1h", [])
    ls_str = f"[{','.join(map(str, ls_series))}]" if ls_valid else "无效"

    taker_series = raw_view.get("taker_ratio_series_1h", [])
    taker_str = f"[{','.join(map(str, taker_series))}]" if taker_series else "无"

    if entry_candidates is None:
        entry_candidates = {"rule1": {"low": 0.0, "high": 0.0}, "rule2": {"low": 0.0, "high": 0.0}, "rule3": {"low": 0.0, "high": 0.0}}
    if tp_candidates is None:
        tp_candidates = {"rule1": {"price": 0.0}, "rule2": {"price": 0.0}, "rule3": {"price": 0.0}}

    prompt = f"""你是一位专注于加密衍生品与行为金融学的高级量化交易员，精通清算动力学、多空博弈分析及数据分析，请严格执行以下命令，独立研判，有最终裁决权，制定一份合约交易策略。
{extreme_liq_text}{warning_text}{trend_desc}

【现价】{price} ATR{atr:.2f} 波动{volatility_factor:.2f}
【清算】上{coinglass_data.get('above_short_liquidation','N/A')} 下{coinglass_data.get('below_long_liquidation','N/A')} 痛点{liq_max_pain} 最近{cluster.get('direction','N/A')}{cluster.get('price','N/A')}强{cluster.get('intensity','N/A')} 动态:{liq_dynamic_text}
【博弈】费率{coinglass_data.get('funding_rate','N/A')}% OI{coinglass_data.get('oi_change_24h','N/A')}% 主动比{coinglass_data.get('taker_ratio','N/A')} 顶级多空{coinglass_data.get('top_long_short_ratio','N/A')} 净持仓{coinglass_data.get('net_position_cum','N/A')} 订单簿{coinglass_data.get('orderbook_imbalance',0.0):.2f}
【资金】CVD{coinglass_data.get('cvd_signal','N/A')} 钱包:{bal_text}
【宏观】期权痛点{option_pain} 恐贪{fg.get('value','50')}(前{fg.get('prev','50')}) ETH/BTC{eth_btc_trend}({eth_btc_ratio:.6f}) 信号:{macro_signals_text}
【量化】多头{bull_score}vs空头{bear_score} {higher_direction}领先{diff}分 评级{signal_grade}

📁原始数据
前三强清算:{top_3_str}
CVD:{cvd_str}
多空比序列:{ls_str}
主动买卖序列:{taker_str}

🔬强制任务(逐项分析输出核心结论，每项以🔍开头，不超过40字)
1.清算不对称:比值=？是否≥2或≤0.5？最强三档价格及ATR。
2.CVD趋势:序列趋势，前后半段变化，与价格背离否？(无效则跳过)
3.持仓矛盾:顶级多空比vs净持仓一致性。
4.清算信号验证:系统信号在分布表存在否？
5.宏观边际:恐贪变化，稳定币7日变化超±1%否？
6.主动买卖比:当前值及方向，持续性。
7.订单簿失衡:当前值及偏向，与主动买卖同向否？
8.ETH/BTC趋势:方向及对{symbol}影响。
9.钱包余额:BTC与稳定币流向，资金面偏多/空。
10.反驳系统({higher_direction}):故意找反驳理由，无则解释。

⚖️裁决:系统建议分差≥{threshold_bull_bear}时可参考，你有权否决。
🎯入场候选:1({entry_candidates['rule1']['low']:.1f}-{entry_candidates['rule1']['high']:.1f}) 2({entry_candidates['rule2']['low']:.1f}-{entry_candidates['rule2']['high']:.1f}) 3({entry_candidates['rule3']['low']:.1f}-{entry_candidates['rule3']['high']:.1f})
止盈候选:A{tp_candidates['rule1']['price']:.1f} B{tp_candidates['rule2']['price']:.1f} C{tp_candidates['rule3']['price']:.1f}(2:1)

输出纯JSON(不要代码块):
{{"direction":"long/short/neutral","confidence":"high/medium/low","entry_price_low":0,"entry_price_high":0,"stop_loss":0,"take_profit":0,"tp_anchor":"","analysis_summary":"🔍1.xxx\\n🔍2.xxx\\n...\\n【最终裁决】...","trader_commentary":"","risk_note":""}}
"""
    return prompt


def call_deepseek(prompt: str, max_retries: int = 3) -> dict:
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1", timeout=120.0)
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
