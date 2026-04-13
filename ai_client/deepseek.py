import os
import json
from openai import OpenAI
from utils.logger import logger

def calculate_win_rate(direction: str, coinglass_data: dict, macro_data: dict, profile: dict) -> int:
    """基于可量化指标严格计算胜率"""
    base_win_rate = profile["base_win_rate"]
    signals = profile["signals"]
    fg = macro_data.get("fear_greed", {})
    fg_value = int(fg.get("value", 50))

    score = 0

    # 1. 清算结构明确性
    above = coinglass_data.get("above_short_liquidation", "0")
    below = coinglass_data.get("below_long_liquidation", "0")
    try:
        above_val = float(above.replace(",", "")) if isinstance(above, str) else float(above)
        below_val = float(below.replace(",", "")) if isinstance(below, str) else float(below)
        if above_val > 0 and below_val > 0:
            diff = abs(above_val - below_val) / max(above_val, below_val)
            if diff > 0.3:
                liq_direction = "long" if above_val > below_val else "short"
                if liq_direction == direction:
                    score += signals.get("liquidation", {}).get("weight", 10)
    except:
        pass

    # 2. 资金费率极端性
    funding_rate = coinglass_data.get("funding_rate", "N/A")
    try:
        fr = float(funding_rate)
        if fr > 0.05 and direction == "short":
            score += signals.get("funding_rate", {}).get("weight", 10)
        elif fr < -0.02 and direction == "long":
            score += signals.get("funding_rate", {}).get("weight", 10)
    except:
        pass

    # 3. 顶级交易员极端性
    top_ls = coinglass_data.get("top_long_short_ratio", "N/A")
    try:
        tls = float(top_ls)
        if tls > 2.0 and direction == "short":
            score += signals.get("top_trader", {}).get("weight", 10)
        elif tls < 0.7 and direction == "long":
            score += signals.get("top_trader", {}).get("weight", 10)
    except:
        pass

    # 4. CVD 同向性
    cvd_signal = coinglass_data.get("cvd_signal", "N/A")
    if (direction == "long" and cvd_signal in ["bullish", "slightly_bullish"]) or \
       (direction == "short" and cvd_signal in ["bearish", "slightly_bearish"]):
        score += signals.get("cvd", {}).get("weight", 10)

    # 5. 恐惧贪婪极端性
    if fg_value < 20 and direction == "long":
        score += signals.get("fear_greed", {}).get("weight", 10)
    elif fg_value > 80 and direction == "short":
        score += signals.get("fear_greed", {}).get("weight", 10)

    # 扣分项：数据缺失
    na_count = sum(1 for v in [coinglass_data.get("above_short_liquidation"),
                               coinglass_data.get("top_long_short_ratio"),
                               coinglass_data.get("cvd_signal")] if v == "N/A")
    score -= min(10, na_count * 3)

    win_rate = base_win_rate + score
    return max(40, min(profile["max_win_rate"], win_rate))


def calculate_signal_strength(direction: str, coinglass_data: dict, macro_data: dict) -> dict:
    """计算信号共振数量，返回强度等级和详情"""
    signals = []
    
    # 清算方向
    above = coinglass_data.get("above_short_liquidation", "0")
    below = coinglass_data.get("below_long_liquidation", "0")
    try:
        above_val = float(above.replace(",", "")) if isinstance(above, str) else float(above)
        below_val = float(below.replace(",", "")) if isinstance(below, str) else float(below)
        if above_val > below_val * 1.3:
            signals.append("清算偏多")
        elif below_val > above_val * 1.3:
            signals.append("清算偏空")
    except:
        pass

    # 顶级交易员
    top_ls = coinglass_data.get("top_long_short_ratio", "N/A")
    try:
        tls = float(top_ls)
        if tls > 2.0:
            signals.append("顶级偏空")
        elif tls < 0.7:
            signals.append("顶级偏多")
    except:
        pass

    # CVD
    cvd = coinglass_data.get("cvd_signal", "N/A")
    if cvd in ["bullish", "bearish", "slightly_bullish", "slightly_bearish"]:
        signals.append(f"CVD:{cvd}")

    # 恐惧贪婪
    fg = macro_data.get("fear_greed", {})
    fg_val = int(fg.get("value", 50))
    if fg_val < 20:
        signals.append("极度恐惧(偏多)")
    elif fg_val > 80:
        signals.append("极度贪婪(偏空)")

    strength = len(signals)
    if strength >= 3:
        level = "强"
    elif strength >= 2:
        level = "中"
    else:
        level = "弱"

    return {"level": level, "count": strength, "details": signals}


def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict, profile: dict, volatility_factor: float = 1.0) -> str:
    fg = macro_data.get("fear_greed", {})
    signals = profile["signals"]

    signal_desc = ""
    for name, cfg in signals.items():
        if cfg["reliable"]:
            signal_desc += f"- {name}: 可信，权重 {cfg['weight']}%\n"
        else:
            signal_desc += f"- {name}: 不可用，不计入评分\n"

    stop_rule = f"止损距离 = max({profile['stop_multiplier']} × ATR, 最近清算密集区距离 × 1.2)"
    position_rule = f"基准仓位 {profile['base_position']*100:.0f}%，最大 {profile['max_position']*100:.0f}%。"
    if volatility_factor > 1.5:
        position_rule += f" 当前波动率因子 {volatility_factor:.2f} > 1.5，仓位需乘以 {profile['volatility_discount']}。"

    cluster = coinglass_data.get("nearest_cluster", {})
    cluster_direction = cluster.get("direction", "N/A")
    cluster_price_raw = cluster.get("price", "N/A")
    cluster_intensity = cluster.get("intensity", "N/A")
    option_pain = coinglass_data.get("skew", "N/A")

    min_profit_distance = max(0.5 * atr, price * 0.003)

    return f"""你是一位顶尖的加密货币短线合约交易员，专精于**清算动力学**、**多空博弈分析**。请根据以下实时市场数据，为{symbol}永续合约制定一份具体的短线交易策略（持仓周期4-24小时）。

### 当前市场数据
**基础信息**
- 当前价格：{price} USDT
- 1小时ATR(14)：{atr} USDT
- 波动率因子：{volatility_factor}（>1.5 为高波动，<0.8 为低波动）
- **最小盈利空间阈值**：{min_profit_distance:.1f} USDT（止盈锚点与当前价的距离必须 ≥ 此值）

**清算压力数据**
- 上方空头清算累计金额：{coinglass_data.get('above_short_liquidation', 'N/A')} USD
- 下方多头清算累计金额：{coinglass_data.get('below_long_liquidation', 'N/A')} USD
- 清算最大痛点：{coinglass_data.get('max_pain_price', 'N/A')} USDT
- **最近清算密集区**：{cluster_direction}方，价格 {cluster_price_raw} USDT，强度 {cluster_intensity}/5
  * 强度≥3/5 的区域是强力磁吸位，价格大概率会触及。

**多空博弈数据**
- 资金费率：{coinglass_data.get('funding_rate', 'N/A')}%
- 持仓量24h变化：{coinglass_data.get('oi_change_24h', 'N/A')}%
- 主动吃单量比率（OKX）：{coinglass_data.get('taker_ratio', 'N/A')}
- 全局多空比：{coinglass_data.get('long_short_ratio', 'N/A')}
- 顶级交易员多空比：{coinglass_data.get('top_long_short_ratio', 'N/A')}
- 净持仓累积变化：{coinglass_data.get('net_position_cum', 'N/A')}（正值=净多头累积）

**资金流向与情绪**
- CVD信号：{coinglass_data.get('cvd_signal', 'N/A')}（斜率：{coinglass_data.get('cvd_slope', 'N/A')}）
- 聚合主动买卖比率：{coinglass_data.get('aggregated_taker_ratio', 'N/A')}
- 累计资金费率（OKX）：{coinglass_data.get('accumulated_funding_rate', 'N/A')}

**期权参考**
- 期权最大痛点：{option_pain} USDT（机构博弈核心价位）
- 期权持仓价值：{coinglass_data.get('option_oi_usd', 'N/A')} USD

**宏观背景**
- 恐惧贪婪指数：{fg.get('value', '50')}（{fg.get('classification', 'Neutral')}）

### {symbol} 专属信号配置
{signal_desc}

### 策略输出要求
请严格按照以下JSON格式输出：
{{
  "direction": "long" 或 "short" 或 "neutral",
  "confidence": "high" 或 "medium" 或 "low",
  "win_rate": 0,
  "entry_price_low": 入场区间下限,
  "entry_price_high": 入场区间上限,
  "stop_loss": 止损价,
  "take_profit_1": 第一止盈价,
  "tp1_anchor": "TP1的锚定来源",
  "take_profit_2": 第二止盈价,
  "tp2_anchor": "TP2的锚定来源",
  "position_size_ratio": 仓位比例（0.0-1.0）,
  "reasoning": "1-2句话核心逻辑，必须提及止盈锚定依据",
  "risk_note": "风险提示"
}}

### 止盈方向强制校验（最高优先级，违反即视为无效策略）

- **做多时**：TP1 和 TP2 必须 **严格大于** 入场价（取入场区间上限）。
- **做空时**：TP1 和 TP2 必须 **严格小于** 入场价（取入场区间下限）。
- 若某个锚点不满足方向要求，**绝对不得**将其作为止盈目标。必须跳过该锚点，寻找下一个同向锚点。
- 若找不到任何同向锚点满足方向与盈利空间要求，则输出 `direction: "neutral"`，并在 reasoning 中说明：“无有效止盈目标”。

### 止盈锚点选择与盈利空间校验

**1. 盈利空间校验（入场窗口过滤）**
- 做多时，TP1 锚点价格 - 当前价 必须 ≥ {min_profit_distance:.1f} USDT。
- 做空时，当前价 - TP1 锚点价格 必须 ≥ {min_profit_distance:.1f} USDT。
- **若不满足**，必须跳过该锚点，寻找下一个有效锚点。

**2. TP1 锚点选择**
- 优先选择满足盈利空间要求的**最近清算密集区**（强度≥3/5）。
- 若无，选择**期权最大痛点**（若满足盈利空间且方向正确）。
- 若以上均无，使用 **1.5×ATR** 作为替代，并在 tp1_anchor 中注明“ATR估算”。

**3. TP2 锚点选择与分层**
- TP2 必须选择距离 TP1 ≥ 0.3×ATR 的同向锚点。
- 优先选择：下一个清算密集区 > 期权最大痛点 > 前高/前低。
- 若无法找到满足分层要求的 TP2，可仅输出 TP1，并将 TP2 设为与 TP1 相同，并在 reasoning 中说明。

### 信号稳定性约束（防止频繁翻转）
- 若当前分析的方向与近期市场惯性相悖，且信号共振强度不足，应优先输出 `direction: "neutral"`。
- 在 reasoning 中说明：“市场方向不明，建议等待确认”。

### 止损设定规则
- {stop_rule}
- 做多时止损必须低于入场价，做空时止损必须高于入场价。

### 仓位设定规则
- {position_rule}

### 特别提醒
- 清算密集区是价格最可能被吸引到达的位置，但若距离过近则盈利空间不足，必须跳过。
- 若净持仓累积与价格走势背离，是强力的反转信号。
- 所有价格保留1位小数。
- **胜率由系统自动计算，你无需填写，请将 win_rate 设为 0。**
"""


def call_deepseek(prompt: str, max_retries: int = 2) -> dict:
    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1"
    )
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000
            )
            content = response.choices[0].message.content
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start == -1 or json_end == 0:
                raise ValueError("未找到 JSON")
            json_str = content[json_start:json_end]
            strategy = json.loads(json_str)
            if "win_rate" not in strategy:
                strategy["win_rate"] = 0
            if "tp1_anchor" not in strategy:
                strategy["tp1_anchor"] = "未提供"
            if "tp2_anchor" not in strategy:
                strategy["tp2_anchor"] = "未提供"
            return strategy
        except Exception as e:
            logger.warning(f"DeepSeek 调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                raise
    return {}


def validate_strategy(strategy: dict, current_price: float) -> bool:
    if "direction" not in strategy:
        return False
    direction = strategy["direction"]
    if direction not in ["long", "short", "neutral"]:
        return False
    if direction == "neutral":
        return True

    required = ["entry_price_low", "entry_price_high", "stop_loss"]
    for field in required:
        if field not in strategy or strategy[field] in [None, ""]:
            return False
        try:
            float(strategy[field])
        except:
            return False

    entry_low = float(strategy["entry_price_low"])
    entry_high = float(strategy["entry_price_high"])
    stop = float(strategy["stop_loss"])

    if direction == "long" and stop >= entry_low:
        logger.warning("做多时止损必须低于入场价")
        return False
    if direction == "short" and stop <= entry_high:
        logger.warning("做空时止损必须高于入场价")
        return False

    tp1 = strategy.get("take_profit_1")
    tp2 = strategy.get("take_profit_2")
    if tp1 is not None and tp1 != "":
        try:
            tp1_val = float(tp1)
            entry_ref = entry_low if direction == "long" else entry_high
            if direction == "long" and tp1_val <= entry_ref:
                logger.warning(f"做多时 TP1({tp1_val}) 必须大于入场价({entry_ref})")
                return False
            if direction == "short" and tp1_val >= entry_ref:
                logger.warning(f"做空时 TP1({tp1_val}) 必须小于入场价({entry_ref})")
                return False
        except:
            pass

    if tp2 is not None and tp2 != "":
        try:
            tp2_val = float(tp2)
            entry_ref = entry_low if direction == "long" else entry_high
            if direction == "long" and tp2_val <= entry_ref:
                logger.warning(f"做多时 TP2({tp2_val}) 必须大于入场价({entry_ref})")
                return False
            if direction == "short" and tp2_val >= entry_ref:
                logger.warning(f"做空时 TP2({tp2_val}) 必须小于入场价({entry_ref})")
                return False
        except:
            pass

    return True
