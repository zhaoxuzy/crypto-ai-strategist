import os
import json
from openai import OpenAI
from utils.logger import logger


def build_prompt(data: dict, symbol: str) -> str:
    timestamp = data.get("timestamp", "N/A")
    current = data['mark_price']
    above_cluster = data.get('above_cluster', 'N/A')
    below_cluster = data.get('below_cluster', 'N/A')
    
    above_distance = "N/A"
    below_distance = "N/A"
    if above_cluster != 'N/A' and '-' in above_cluster:
        above_high = float(above_cluster.split('-')[1])
        above_distance = f"+{above_high - current:.0f}"
    if below_cluster != 'N/A' and '-' in below_cluster:
        below_low = float(below_cluster.split('-')[0])
        below_distance = f"-{current - below_low:.0f}"

    data_quality = data.get("data_quality", {})
    missing = [k for k, v in data_quality.items() if v == "❌ 缺失"]
    missing_str = "、".join(missing) if missing else "无"

    prompt = f"""角色：资深加密货币短线交易员，管理 200 万 U 资金。
任务：基于以下市场数据，形成完整的交易决策。请确保你的分析覆盖全部四个维度。

【{symbol} | {timestamp}】

一、价格与波动背景
标记价格：{current:.2f}
15min ATR：{data['atr_15m']:.2f}
波动因子：{data['vol_factor']:.2f}
7日价格分位数：{data['price_percentile']:.0f}%

二、流动性战场
上方空头清算：{data['above_liq']/1e9:.2f}B，密集区 {above_cluster} (距{above_distance})
下方多头清算：{data['below_liq']/1e9:.2f}B，密集区 {below_cluster} (距{below_distance})
上方/下方比值：{data['liq_ratio']:.3f}
订单簿：买盘 {data['orderbook_bids']/1e6:.1f}M / 卖盘 {data['orderbook_asks']/1e6:.1f}M，失衡率 {data['orderbook_imbalance']:.4f}

三、情绪与持仓结构
资金费率：{data['funding_rate']:.4f}% (7日分位{data['funding_percentile']:.0f}%)
OI：{data['oi']/1e9:.2f}B (分位{data['oi_percentile']:.0f}%)，24h变化 {data['oi_change_24h']:+.1f}%
全市场OI：{data['agg_oi']/1e9:.2f}B，24h变化 {data['agg_oi_change_24h']:+.1f}%
顶级多空比：{data['top_ls_ratio']:.2f} (分位{data['top_ls_percentile']:.0f}%)
恐慌贪婪：{data['fear_greed']} (7日前 {data['fear_greed_prev_7d']})
期权最大痛点：{data['max_pain']:.2f}
Put/Call Ratio：{data['put_call_ratio']:.4f}

四、资金流向验证
CVD斜率：{data['cvd_slope']:.4f}
期货24h净流：{data['netflow']/1e6:.1f}M USDT
交易所BTC 24h变化：{data['exchange_btc_change_pct']:+.2f}%
ETH/BTC汇率：{data['eth_btc_ratio']:.4f}

数据缺失项：{missing_str}

---
请按以下框架组织你的完整分析（这是你作为交易员的思考习惯，每个维度都必须有明确结论）：

1. 环境定性：基于价格分位数、波动因子、ATR，判断当前市场处于什么状态（趋势/震荡/高低位）。
2. 猎物与战场：对比上下方清算池的吸引力，评估订单簿的厚薄，确定价格最可能被牵引的方向。
3. 情绪与对手盘：综合OI、资金费率、顶级多空比、恐慌贪婪趋势、期权信号，判断市场拥挤度与谁在犯错。
4. 资金面验证：CVD、期货净流、交易所余额、ETH/BTC是否支持你的方向判断？是否存在背离？
5. 交易计划：止损位的数据依据，止盈目标的选择，盈亏比评估，仓位配置。
6. 风险预案：策略失效的关键信号及应对措施。

输出JSON格式（不要代码块）：
{{
  "direction": "long/short/neutral",
  "confidence": "high/medium/low",
  "position_size": "light/medium/heavy/none",
  "entry_price_low": 0.0,
  "entry_price_high": 0.0,
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "execution_plan": "一句话指令。",
  "reasoning": "按上述框架展开的完整分析。",
  "risk_note": "最坏情况的预案。"
}}
"""
    return prompt


def call_deepseek(prompt: str, max_retries: int = 3) -> dict:
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1", timeout=120.0)
    for attempt in range(max_retries):
        try:
            logger.info(f"DeepSeek Reasoner API 调用 (尝试 {attempt+1}/{max_retries})，Prompt 长度: {len(prompt)} 字符")
            resp = client.chat.completions.create(
                model="deepseek-reasoner",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000
            )
            content = resp.choices[0].message.content
            logger.info(f"DeepSeek Reasoner 响应成功，原始内容长度: {len(content)}")

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
                logger.warning(f"DeepSeek Reasoner 返回无有效 JSON")
                if attempt == max_retries - 1:
                    raise ValueError("无法提取 JSON")
                continue

            s = json.loads(json_str)
            s.setdefault("position_size", "none")
            s.setdefault("execution_plan", "")
            s.setdefault("risk_note", "")
            return s
        except Exception as e:
            logger.warning(f"DeepSeek Reasoner 调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                raise
    return {}


def validate_strategy(s: dict) -> tuple[bool, str]:
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
            return False, "价格必须为正数"
        if entry_low > entry_high:
            return False, "入场区间下限大于上限"
    except:
        return False, "数值解析失败"
    return True, ""