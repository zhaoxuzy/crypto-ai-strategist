import os
import json
from openai import OpenAI
from utils.logger import logger

# ---------- 连续型评分辅助函数 ----------
def linear_score(value: float, low: float, high: float, full_score: float, reverse: bool = False) -> float:
    """
    分段线性计分：值在 [low, high] 区间内线性映射到 [0, full_score]。
    reverse=True 表示值越低得分越高（如恐惧贪婪 <20 给满分）。
    """
    if low == high:
        return 0.0
    if reverse:
        if value <= low:
            return full_score
        if value >= high:
            return 0.0
        return full_score * (high - value) / (high - low)
    else:
        if value <= low:
            return 0.0
        if value >= high:
            return full_score
        return full_score * (value - low) / (high - low)


def get_position_structure_score(direction: str, coinglass_data: dict, macro_data: dict) -> tuple:
    """
    合并后的持仓结构因子：融合顶级交易员多空比和多空持仓人数比。
    返回 (得分, 详情列表)
    """
    score = 0.0
    details = []
    
    # 顶级交易员多空比（权重更高）
    top_ls = coinglass_data.get("top_long_short_ratio", "N/A")
    try:
        tls = float(top_ls)
        if direction == "long":
            if tls <= 0.7:
                s = 20.0
            elif tls <= 2.0:
                s = linear_score(tls, 0.7, 2.0, 20, reverse=True)
            else:
                s = 0.0
        else:  # short
            if tls >= 2.0:
                s = 20.0
            elif tls >= 0.7:
                s = linear_score(tls, 0.7, 2.0, 20, reverse=False)
            else:
                s = 0.0
        score += s
        if s > 1:
            details.append(f"顶级持仓结构({tls:.2f})")
        elif tls != "N/A" and ((direction == "long" and tls > 2.0) or (direction == "short" and tls < 0.7)):
            score -= 20 * 0.4
            details.append(f"顶级持仓反向({tls:.2f})")
    except:
        pass
    
    # 多空持仓人数比（补充权重）
    ls_account = coinglass_data.get("ls_account_ratio", 1.0)
    try:
        lsa = float(ls_account)
        if direction == "long":
            if lsa <= 0.7:
                s = 12.0
            elif lsa <= 2.0:
                s = linear_score(lsa, 0.7, 2.0, 12, reverse=True)
            else:
                s = 0.0
        else:
            if lsa >= 2.0:
                s = 12.0
            elif lsa >= 0.7:
                s = linear_score(lsa, 0.7, 2.0, 12, reverse=False)
            else:
                s = 0.0
        score += s
        if s > 1:
            details.append(f"人数比({lsa:.2f})")
        elif lsa != 1.0 and ((direction == "long" and lsa > 2.0) or (direction == "short" and lsa < 0.7)):
            score -= 12 * 0.3
            details.append(f"人数比反向({lsa:.2f})")
    except:
        pass
    
    return score, details


# ---------- 信号强度计算（重构版）----------
def calculate_signal_strength(direction: str, coinglass_data: dict, macro_data: dict, liq_zero_count: int = 0) -> dict:
    """
    计算加权信号强度得分（满分100分），同时输出胜率。
    包含连续型评分、反向扣分、共线性合并。
    """
    total_score = 0.0
    max_score = 100.0
    signals_detail = []
    
    # ---- 1. 清算方向（28分）----
    above = coinglass_data.get("above_short_liquidation", "0")
    below = coinglass_data.get("below_long_liquidation", "0")
    try:
        above_val = float(above.replace(",", "")) if isinstance(above, str) else float(above)
        below_val = float(below.replace(",", "")) if isinstance(below, str) else float(below)
        if above_val > 0 or below_val > 0:
            total_liq = above_val + below_val
            if total_liq > 0:
                short_ratio = above_val / total_liq
                if direction == "short":
                    s = linear_score(short_ratio, 0.5, 0.8, 28, reverse=False)
                else:
                    s = linear_score(short_ratio, 0.2, 0.5, 28, reverse=True)
                total_score += s
                if s > 5:
                    signals_detail.append(f"清算结构({short_ratio:.1%})")
                if (direction == "long" and short_ratio > 0.6) or (direction == "short" and short_ratio < 0.4):
                    total_score -= 28 * 0.4
                    signals_detail.append("清算结构反向")
    except:
        pass

    # ---- 2. 持仓结构因子（合并顶级+人数比，32分）----
    pos_score, pos_details = get_position_structure_score(direction, coinglass_data, macro_data)
    total_score += pos_score
    signals_detail.extend(pos_details)

    # ---- 3. CVD（12分）----
    cvd = coinglass_data.get("cvd_signal", "N/A")
    cvd_slope = coinglass_data.get("cvd_slope", 0.0)
    if cvd in ["bullish", "slightly_bullish"]:
        if direction == "long":
            s = 12.0 if cvd == "bullish" else 8.0
            total_score += s
            signals_detail.append(f"CVD:{cvd}")
        else:
            total_score -= 12 * 0.5
            signals_detail.append("CVD反向")
    elif cvd in ["bearish", "slightly_bearish"]:
        if direction == "short":
            s = 12.0 if cvd == "bearish" else 8.0
            total_score += s
            signals_detail.append(f"CVD:{cvd}")
        else:
            total_score -= 12 * 0.5
            signals_detail.append("CVD反向")

    # ---- 4. 恐惧贪婪（连续型，8分）----
    fg = macro_data.get("fear_greed", {})
    fg_val = int(fg.get("value", 50))
    if direction == "long":
        s = linear_score(fg_val, 20, 50, 8, reverse=True)
    else:
        s = linear_score(fg_val, 50, 80, 8, reverse=False)
    total_score += s
    if s > 2:
        signals_detail.append(f"恐惧贪婪({fg_val})")
    if (direction == "long" and fg_val > 70) or (direction == "short" and fg_val < 30):
        total_score -= 8 * 0.4
        signals_detail.append("情绪反向")

    # ---- 5. 资金费率（连续型，5分）----
    funding_rate = coinglass_data.get("funding_rate", "N/A")
    try:
        fr = float(funding_rate)
        if direction == "short":
            s = linear_score(fr, 0.02, 0.08, 5, reverse=False)
        else:
            s = linear_score(fr, -0.08, -0.01, 5, reverse=True)
        total_score += s
        if abs(s) > 1:
            signals_detail.append(f"资金费率({fr:.4f})")
        if (direction == "long" and fr > 0.03) or (direction == "short" and fr < -0.03):
            total_score -= 5 * 0.5
            signals_detail.append("费率反向")
    except:
        pass

    # ---- 6. 主动买盘比率（8分）----
    taker_ratio = coinglass_data.get("taker_ratio", "N/A")
    try:
        tr = float(taker_ratio)
        if direction == "long":
            s = linear_score(tr, 0.5, 0.65, 8, reverse=False)
        else:
            s = linear_score(tr, 0.35, 0.5, 8, reverse=True)
        total_score += s
        if s > 2:
            signals_detail.append(f"主动买盘({tr:.2f})")
        if (direction == "long" and tr < 0.45) or (direction == "short" and tr > 0.55):
            total_score -= 8 * 0.5
            signals_detail.append("主动方向反向")
    except:
        pass

    # ---- 7. 净持仓累积（6分）----
    net_pos = coinglass_data.get("net_position_cum", "N/A")
    try:
        np = float(net_pos)
        if direction == "long":
            s = linear_score(np, 500, 2000, 6, reverse=False)
        else:
            s = linear_score(np, -2000, -500, 6, reverse=True)
        total_score += s
        if abs(s) > 1:
            signals_detail.append(f"净持仓({np:.0f})")
        if (direction == "long" and np < -500) or (direction == "short" and np > 500):
            total_score -= 6 * 0.5
            signals_detail.append("净持仓反向")
    except:
        pass

    # ---- 8. 订单簿失衡率（12分）----
    imbalance = coinglass_data.get("orderbook_imbalance", 0.0)
    if direction == "long":
        s = linear_score(imbalance, 0.1, 0.3, 12, reverse=False)
    else:
        s = linear_score(imbalance, -0.3, -0.1, 12, reverse=True)
    total_score += s
    if abs(s) > 3:
        signals_detail.append(f"订单簿({imbalance:.2f})")
    if (direction == "long" and imbalance < -0.15) or (direction == "short" and imbalance > 0.15):
        total_score -= 12 * 0.4
        signals_detail.append("订单簿反向")

    # ---- 数据缺失扣分 ----
    na_count = sum(1 for v in [coinglass_data.get("above_short_liquidation"),
                               coinglass_data.get("top_long_short_ratio"),
                               coinglass_data.get("cvd_signal")] if v == "N/A")
    total_score -= min(8, na_count * 2)

    # 限制得分范围
    total_score = max(-20, min(100, total_score))
    
    # 映射等级
    if total_score >= 75:
        level = "极强"
    elif total_score >= 55:
        level = "强"
    elif total_score >= 35:
        level = "中"
    elif total_score >= 15:
        level = "弱"
    else:
        level = "极弱"

    # 由信号强度映射胜率 (40% ~ 85%)
    win_rate = int(40 + (total_score / 100) * 45)
    win_rate = max(40, min(85, win_rate))

    # 清算数据连续为零强制 neutral 和低胜率
    if liq_zero_count >= 2:
        level = "极弱"
        total_score = max(0, total_score - 30)
        win_rate = max(35, win_rate - 20)

    return {
        "level": level,
        "score": round(total_score, 1),
        "max_score": 100,
        "details": signals_detail,
        "win_rate": win_rate
    }


# ---------- 胜率计算直接复用信号强度 ----------
def calculate_win_rate(direction: str, coinglass_data: dict, macro_data: dict, profile: dict, market_regime: dict = None, liq_zero_count: int = 0) -> int:
    strength = calculate_signal_strength(direction, coinglass_data, macro_data, liq_zero_count)
    return strength["win_rate"]


# ---------- Prompt 构建 ----------
def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict, profile: dict, volatility_factor: float = 1.0, market_regime: dict = None, liq_warning: str = "", data_source_status: str = "") -> str:
    fg = macro_data.get("fear_greed", {})
    signals = profile["signals"]
    
    signal_desc = "各指标权重已由系统动态计算，你只需关注综合方向。"
    
    stop_rule = f"止损距离 = max({profile['stop_multiplier']} × ATR, 最近清算密集区距离 × 1.2)"
    position_rule = f"基准仓位 {profile['base_position']*100:.0f}%，最大 {profile['max_position']*100:.0f}%。"
    
    cluster = coinglass_data.get("nearest_cluster", {})
    cluster_direction = cluster.get("direction", "N/A")
    cluster_price_raw = cluster.get("price", "N/A")
    cluster_intensity = cluster.get("intensity", "N/A")
    option_pain = coinglass_data.get("skew", "N/A")
    liq_max_pain = coinglass_data.get("max_pain_price", "N/A")

    warning_text = f"\n{liq_warning}\n" if liq_warning else ""
    data_source_text = f"\n**{data_source_status}**\n" if data_source_status else ""

    return f"""你是一位顶尖的加密货币短线合约交易员，专精于**清算动力学**、**多空博弈分析**。请根据以下实时市场数据，为{symbol}永续合约制定一份具体的短线交易策略（持仓周期4-24小时），必须按照要求执行，不得简化。

{warning_text}
{data_source_text}

### 核心市场数据
**价格与波动**
- 当前价格：{price} USDT
- 1小时ATR：{atr} USDT
- 波动因子：{volatility_factor:.2f}

**清算压力**
- 上方空头清算：{coinglass_data.get('above_short_liquidation', 'N/A')} USD
- 下方多头清算：{coinglass_data.get('below_long_liquidation', 'N/A')} USD
- 清算最大痛点：{liq_max_pain} USDT
- 最近清算密集区：{cluster_direction}方 {cluster_price_raw} USDT，强度{cluster_intensity}/5

**多空博弈**
- 资金费率：{coinglass_data.get('funding_rate', 'N/A')}%
- 持仓量24h变化：{coinglass_data.get('oi_change_24h', 'N/A')}%
- 主动吃单比率：{coinglass_data.get('taker_ratio', 'N/A')}
- 顶级交易员多空比：{coinglass_data.get('top_long_short_ratio', 'N/A')}
- 净持仓累积：{coinglass_data.get('net_position_cum', 'N/A')}
- 订单簿失衡率：{coinglass_data.get('orderbook_imbalance', 0.0):.2f}

**资金流向**
- CVD信号：{coinglass_data.get('cvd_signal', 'N/A')}

**期权与宏观**
- 期权最大痛点：{option_pain} USDT
- 恐惧贪婪指数：{fg.get('value', '50')}

### 策略输出要求
请严格按JSON格式输出：
{{
  "direction": "long" 或 "short" 或 "neutral",
  "confidence": "high" 或 "medium" 或 "low",
  "entry_price_low": 入场区间下限,
  "entry_price_high": 入场区间上限,
  "stop_loss": 止损价,
  "take_profit_1": 第一止盈价,
  "tp1_anchor": "TP1锚定来源",
  "take_profit_2": 第二止盈价,
  "tp2_anchor": "TP2锚定来源",
  "position_size_ratio": 仓位比例（0.0-1.0）,
  "reasoning": "核心逻辑（1-2句）",
  "risk_note": "风险提示"
}}

### 止盈锚定原则
- TP1 优先锚定最近清算密集区（强度≥3/5）或期权最大痛点，且盈利空间需≥0.8×ATR且≤3×ATR。
- TP2 锚定下一个清算区或清算最大痛点，需与TP1保持分层距离。
- 若有效锚点不足，可使用1.5×ATR估算。

### 止损与仓位
- {stop_rule}
- {position_rule}
- 所有价格保留1位小数。
"""


def call_deepseek(prompt: str, max_retries: int = 2) -> dict:
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}], temperature=0.3, max_tokens=800)
            content = response.choices[0].message.content
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start == -1 or json_end == 0:
                raise ValueError("未找到 JSON")
            json_str = content[json_start:json_end]
            strategy = json.loads(json_str)
            strategy.setdefault("win_rate", 0)
            strategy.setdefault("tp1_anchor", "未提供")
            strategy.setdefault("tp2_anchor", "未提供")
            strategy.setdefault("is_probe", False)
            return strategy
        except Exception as e:
            logger.warning(f"DeepSeek 调用失败: {e}")
            if attempt == max_retries - 1: raise
    return {}


def validate_strategy(strategy: dict, current_price: float) -> bool:
    if "direction" not in strategy: return False
    direction = strategy["direction"]
    if direction not in ["long", "short", "neutral"]: return False
    if direction == "neutral": return True
    required = ["entry_price_low", "entry_price_high", "stop_loss"]
    for field in required:
        if field not in strategy or strategy[field] in [None, ""]: return False
        try: float(strategy[field])
        except: return False
    entry_low = float(strategy["entry_price_low"])
    entry_high = float(strategy["entry_price_high"])
    stop = float(strategy["stop_loss"])
    if direction == "long" and stop >= entry_low: return False
    if direction == "short" and stop <= entry_high: return False
    return True
