import os
import json
from openai import OpenAI
from utils.logger import logger

def build_prompt(symbol: str, price: float, atr: float, coinglass_data: dict, macro_data: dict) -> str:
    fg = macro_data.get("fear_greed", {})
    return f"""你是一位专业的加密货币短线合约交易员。请根据以下实时市场数据，为{symbol}永续合约制定一份具体的短线交易策略（持仓周期4-24小时）。

### 当前市场数据
**基础信息**
- 当前价格：{price} USDT
- 1小时ATR(14)：{atr} USDT（波动率参考）

**清算压力数据**（CoinGlass）
- 上方空头清算累计金额：{coinglass_data.get('above_short_liquidation', 'N/A')} USD
- 下方多头清算累计金额：{coinglass_data.get('below_long_liquidation', 'N/A')} USD
- 清算最大痛点：{coinglass_data.get('max_pain_price', 'N/A')} USDT
- 最近清算密集区：{coinglass_data.get('nearest_cluster', {}).get('direction', 'N/A')}方，价格{coinglass_data.get('nearest_cluster', {}).get('price', 'N/A')} USDT，强度{coinglass_data.get('nearest_cluster', {}).get('intensity', 'N/A')}/5

**衍生品情绪**
- 资金费率：{coinglass_data.get('funding_rate', 'N/A')}%（历史均值约0.01%）
- 持仓量（OI）24h变化：{coinglass_data.get('oi_change_24h', 'N/A')}%
- 主动吃单量比率（Taker Buy/Sell）：{coinglass_data.get('taker_ratio', 'N/A')}
- 全局多空比：{coinglass_data.get('long_short_ratio', 'N/A')}
- **顶级交易员多空比**：{coinglass_data.get('top_long_short_ratio', 'N/A')}（注意：顶级交易员数据比全局数据更具参考价值，其反向信号强度更高）

**期权市场信号**
- 期权最大痛点：{coinglass_data.get('skew', 'N/A')} USDT
- **期权看跌/看涨比率（PCR）**：{coinglass_data.get('put_call_ratio', 'N/A')}（高于0.7代表市场偏恐慌/对冲需求强，低于0.5代表市场偏乐观）
- **隐含波动率（IV）**：{coinglass_data.get('implied_volatility', 'N/A')}（若显著高于历史均值，代表市场恐慌溢价高）

**资金流向（CVD斜率）**
- 5分钟CVD信号：{coinglass_data.get('cvd_signal', 'N/A')}（斜率值：{coinglass_data.get('cvd_slope', 'N/A')}）
  * bullish/slightly_bullish：主动买盘持续流入
  * bearish/slightly_bearish：主动卖盘持续流出
  * neutral：买卖均衡

**宏观背景**
- 恐惧贪婪指数：{fg.get('value', '50')}（{fg.get('classification', 'Neutral')}，较前日变化{fg.get('change', '0')}）
- 交易所BTC余额趋势：{macro_data.get('exchange_balance_trend', 'N/A')}

### 策略输出要求
请严格按照以下JSON格式输出策略，不要添加任何额外文字或解释：

{{
  "direction": "long" 或 "short" 或 "neutral",
  "confidence": "high" 或 "medium" 或 "low",
  "win_rate": 综合评估的胜率百分比（整数，如58，范围0-100），
  "entry_price_low": 入场区间下限,
  "entry_price_high": 入场区间上限,
  "stop_loss": 止损价,
  "take_profit_1": 第一止盈价,
  "take_profit_2": 第二止盈价,
  "position_size_ratio": 建议仓位比例（0.0-1.0）,
  "reasoning": "简要分析逻辑（1-2句话）",
  "risk_note": "需要关注的风险点"
}}

### 胜率评估框架（必须严格参照）
请按以下规则为每个信号打分，然后累加得到基础胜率：

**方向信号（各占10%，可累计）**：
- 清算结构方向明确（上方空头 vs 下方多头差值 >30%）：+10%
- 资金费率极端（>0.05% 偏空，< -0.02% 偏多）：+10%
- 顶级交易员多空比极端（>2.0 偏空，<0.7 偏多）：+10%
- CVD 斜率与价格同向：+10%
- 恐惧贪婪指数极端（<20 偏多，>80 偏空）：+10%

**风险扣分项（各扣5-10%）**：
- 清算结构矛盾（上下方清算金额接近，差值<10%）：-10%
- 期权PCR与清算方向矛盾（如清算偏多但PCR>0.8）：-10%
- 顶级交易员与全局多空比背离：-5%
- 数据缺失（每项N/A扣3%，最多扣10%）

**基础胜率 = 50% + 累计得分。最终胜率 = max(40%, min(85%, 基础胜率))。**

### 其他决策原则
- 止损必须结合清算密集区与ATR设定，确保有技术依据。
- 仓位比例建议在0.1-0.5之间，信心越高、波动率越低时仓位可越大。
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
