import os
import json
from openai import OpenAI
from utils.logger import logger

def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict, profile: dict, volatility_factor: float = 1.0) -> str:
    fg = macro_data.get("fear_greed", {})
    signals = profile["signals"]

    signal_desc = ""
    for name, cfg in signals.items():
        if cfg["reliable"]:
            signal_desc += f"- {name}: 可信，权重 {cfg['weight']}%\n"
        else:
            signal_desc += f"- {name}: 不可用，不计入评分\n"

    scoring_table = "| 加分项 | 权重 | 是否满足 |\n|--------|------|----------|\n"
    for name, cfg in signals.items():
        if cfg["reliable"] and cfg["weight"] > 0:
            scoring_table += f"| {name}信号明确且与方向一致 | +{cfg['weight']}% | |\n"
    scoring_table += "\n| 扣分项 | 扣分 | 是否触发 |\n|--------|------|----------|\n"
    scoring_table += "| 清算结构矛盾（上下方金额接近） | -10% | |\n"
    scoring_table += "| 信号缺失（每项 N/A） | -3% | |\n"

    stop_rule = f"止损距离 = max({profile['stop_multiplier']} × ATR, 最近清算密集区距离 × 1.2)"
    position_rule = f"基准仓位 {profile['base_position']*100:.0f}%，最大 {profile['max_position']*100:.0f}%。"
    if volatility_factor > 1.5:
        position_rule += f" 当前波动率因子 {volatility_factor:.2f} > 1.5，仓位需乘以 {profile['volatility_discount']}。"

    # 提取清算密集区信息
    cluster = coinglass_data.get("nearest_cluster", {})
    cluster_direction = cluster.get("direction", "N/A")
    cluster_price = cluster.get("price", "N/A")
    cluster_intensity = cluster.get("intensity", "N/A")

    # 期权最大痛点
    option_pain = coinglass_data.get("skew", "N/A")

    return f"""你是一位顶尖的加密货币短线合约交易员，专精于**清算动力学**、**多空博弈分析**。请根据以下实时市场数据，为{symbol}永续合约制定一份具体的短线交易策略（持仓周期4-24小时）。

### 当前市场数据
**基础信息**
- 当前价格：{price} USDT
- 1小时ATR(14)：{atr} USDT
- 波动率因子：{volatility_factor}（>1.5 为高波动，<0.8 为低波动）

**清算压力数据（止盈的核心锚点）**
- 上方空头清算累计金额：{coinglass_data.get('above_short_liquidation', 'N/A')} USD
- 下方多头清算累计金额：{coinglass_data.get('below_long_liquidation', 'N/A')} USD
- 清算最大痛点：{coinglass_data.get('max_pain_price', 'N/A')} USDT
- **最近清算密集区**：{cluster_direction}方，价格 {cluster_price} USDT，强度 {cluster_intensity}/5
  * 强度≥3/5 的区域是强力磁吸位，价格大概率会在本周期内触及。

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
- 期权最大痛点：{option_pain} USDT（机构博弈核心价位，可作为止盈锚点）
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
  "win_rate": {profile['base_win_rate']}-{profile['max_win_rate']}之间的整数,
  "entry_price_low": 入场区间下限,
  "entry_price_high": 入场区间上限,
  "stop_loss": 止损价,
  "take_profit_1": 第一止盈价,
  "tp1_anchor": "TP1的锚定来源（如：上方清算密集区/期权最大痛点/ATR估算）",
  "take_profit_2": 第二止盈价,
  "tp2_anchor": "TP2的锚定来源",
  "position_size_ratio": 仓位比例（0.0-1.0）,
  "reasoning": "1-2句话核心逻辑，必须提及止盈锚定依据",
  "risk_note": "风险提示"
}}

### 止盈目标设定规则（必须严格遵守）

**止盈1（TP1）**：
- 做多时，必须设定为 **上方最近清算密集区的下沿价格**（取自「最近清算密集区」的 price 字段）。
- 做空时，必须设定为 **下方最近清算密集区的上沿价格**。
- 若无清算密集区数据（N/A 或强度为0），则使用 **1.5×ATR** 作为替代，但必须在 tp1_anchor 中注明“ATR估算”。

**止盈2（TP2）**：
- 做多时，优先选择 **期权最大痛点**（若 > TP1 且与当前价距离合理），其次选择 **下一个清算密集区**。
- 做空时，优先选择 **期权最大痛点**（若 < TP1 且距离合理），其次选择 **下一个清算密集区**。
- 若无上述数据，则使用 **2.5×ATR** 作为替代，并在 tp2_anchor 中注明。

**强制校验**：
- TP1 必须 > 入场价（做多）或 < 入场价（做空）。
- TP2 必须 > TP1（做多）或 < TP1（做空）。
- 若无法找到符合规则的止盈位，应将置信度降为 low，并在 reasoning 中说明。

### 止损设定规则
- {stop_rule}
- 做多时止损必须低于入场价，做空时止损必须高于入场价。

### 仓位设定规则
- {position_rule}

### 胜率评估框架
基础胜率 = {profile['base_win_rate']}%。请按以下规则打分：
{scoring_table}
最终胜率 = 基础胜率 + 加分 - 扣分，并限制在 {profile['base_win_rate']}%-{profile['max_win_rate']}% 之间。

### 特别提醒
- 清算密集区是价格最可能被吸引到达的位置，务必将其作为首要止盈参考。
- 若净持仓累积与价格走势背离，是强力的反转信号。
- 所有价格保留1位小数。
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
                strategy["win_rate"] = 50
            else:
                strategy["win_rate"] = int(strategy["win_rate"])
            # 确保新字段存在
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
            val = float(strategy[field])
            if abs(val - current_price) / current_price > 0.2:
                logger.warning(f"{field} 偏离当前价超过20%")
        except:
            return False
    entry = float(strategy["entry_price_low"])
    stop = float(strategy["stop_loss"])
    if direction == "long" and stop >= entry:
        return False
    if direction == "short" and stop <= entry:
        return False
    return True
