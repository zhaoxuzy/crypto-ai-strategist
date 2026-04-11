import os
import json
from openai import OpenAI
from utils.logger import logger

def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict) -> str:
    fg = macro_data.get("fear_greed", {})
    return f"""你是一位顶尖的加密货币短线合约交易员，专精于**清算动力学**、**多空博弈分析**以及**图表技术分析**。你的交易周期为4-24小时。

请根据以下实时市场数据，为{symbol}永续合约制定一份具体的、可执行的短线交易策略。

### 一、 市场实时数据

**1. 基础信息**
- 当前价格：{price} USDT
- 1小时ATR(14)：{atr} USDT（用于设定止损宽度）

**2. 清算动力学数据（核心）**
- 上方空头清算累计金额：{coinglass_data.get('above_short_liquidation', 'N/A')} USD
- 下方多头清算累计金额：{coinglass_data.get('below_long_liquidation', 'N/A')} USD
- 清算最大痛点（清算强度最高价格）：{coinglass_data.get('max_pain_price', 'N/A')} USDT
- 最近清算密集区：{coinglass_data.get('nearest_cluster', {}).get('direction', 'N/A')}方，价格{coinglass_data.get('nearest_cluster', {}).get('price', 'N/A')} USDT，强度{coinglass_data.get('nearest_cluster', {}).get('intensity', 'N/A')}/5

**3. 多空博弈数据**
- 资金费率：{coinglass_data.get('funding_rate', 'N/A')}%（历史均值约0.01%，>0.05%为空头信号，< -0.02%为多头信号）
- 持仓量（OI）24h变化：{coinglass_data.get('oi_change_24h', 'N/A')}%（OI与价格同向为趋势确认，背离为反转预警）
- 主动吃单量比率（Taker Buy/Sell）：{coinglass_data.get('taker_ratio', 'N/A')}（>0.55为主动买盘强劲）
- 全局多空比：{coinglass_data.get('long_short_ratio', 'N/A')}（散户情绪参考）
- **顶级交易员多空比**：{coinglass_data.get('top_long_short_ratio', 'N/A')}（>2.0为多头拥挤，<0.7为空头拥挤，此为反向指标）

**4. 资金流向与期权数据**
- 5分钟CVD信号：{coinglass_data.get('cvd_signal', 'N/A')}（斜率值：{coinglass_data.get('cvd_slope', 'N/A')}）
- 期权最大痛点：{coinglass_data.get('skew', 'N/A')} USDT（机构博弈核心价位）
- 期权持仓价值：{coinglass_data.get('option_oi_usd', 'N/A')} USD

**5. 宏观背景**
- 恐惧贪婪指数：{fg.get('value', '50')}（{fg.get('classification', 'Neutral')}，较前日变化{fg.get('change', '0')}）

### 二、 策略输出要求

请严格按照以下JSON格式输出，不要添加任何额外文字。

{{
  "direction": "long" 或 "short" 或 "neutral",
  "confidence": "high" 或 "medium" 或 "low",
  "win_rate": 50-85之间的整数,
  "entry_price_low": 入场区间下限,
  "entry_price_high": 入场区间上限,
  "stop_loss": 止损价,
  "take_profit_1": 第一止盈价,
  "take_profit_2": 第二止盈价,
  "position_size_ratio": 0.1-0.5之间的仓位比例,
  "reasoning": "1-2句话的核心逻辑，必须结合清算动力学或多空博弈",
  "risk_note": "需要关注的风险点"
}}

### 三、 决策框架（你必须内化此思维过程）

1.  **清算图谱定方向**：
    - 比较上下方清算金额。若上方空头清算堆积远大于下方多头，价格易被“磁吸”向上猎杀空头 → **偏多**。反之偏空。
    - 若当前价紧贴某一侧清算密集区，且强度≥3/5，则反向突破该区域前，顺势操作。

2.  **多空博弈选时机**：
    - **寻找“犯错”的一方**：若资金费率极高（>0.05%）且顶级交易员多空比>2.0，说明散户疯狂做多，而聪明钱可能在派发。此时若价格接近上方清算区，是做空的绝佳时机。
    - **CVD确认**：若计划做多，需看到CVD信号为bullish或slightly_bullish；若背离（价格横盘但CVD下降），则为危险信号。

3.  **风控与入场**：
    - **止损**：必须设置在关键清算区之外。做多时，止损设在下方多头清算区下沿 - 0.5倍ATR处；做空时，止损设在上方空头清算区上沿 + 0.5倍ATR处。
    - **仓位**：若顶级交易员与清算方向共振，仓位可增至0.3-0.4；若信号矛盾，仓位降至0.1-0.2。
    - **价格**：所有价格保留1位小数。

### 四、 胜率评估框架

请按以下规则为当前机会打分（累加得出胜率）：

| 加分项（每个+10%） | 是否满足 |
| :--- | :--- |
| 清算结构明确（上下方金额差>30%） | |
| 资金费率极端（>0.05%或<-0.02%）且与方向一致 | |
| 顶级交易员多空比极端（>2.0或<0.7）且与方向一致 | |
| CVD斜率与交易方向同向 | |
| 恐惧贪婪指数极端（<20偏多，>80偏空）且与方向一致 | |

| 扣分项 | 扣分 |
| :--- | :--- |
| 清算结构矛盾（上下方金额接近，差值<10%） | -10% |
| 顶级交易员与全局多空比严重背离 | -5% |
| 关键数据缺失（每项N/A扣3%，最多扣10%） | 最多-10% |

**基础胜率 = 50% + 累计加分 - 累计扣分。最终胜率 = max(40%, min(85%, 基础胜率))。**
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
                max_tokens=900
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
