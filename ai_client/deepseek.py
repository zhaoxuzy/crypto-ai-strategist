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
        above_distance = f"{above_high - current:.0f}"
    if below_cluster != 'N/A' and '-' in below_cluster:
        below_low = float(below_cluster.split('-')[0])
        below_distance = f"{current - below_low:.0f}"

    prompt = f"""【角色】你是一名管理 200 万 U 的顶尖加密货币短线合约交易员。
【任务】基于以下数据，给出你独立的交易决策。不要使用固定模板，用自然的段落表达你的判断。

【{symbol} | {timestamp}】

价格：{current:.2f}
15min ATR：{data['atr_15m']:.2f}

清算池：
- 上方空头清算：{data['above_liq']/1e9:.2f}B，密集区 {above_cluster}，距现价 +{above_distance}
- 下方多头清算：{data['below_liq']/1e9:.2f}B，密集区 {below_cluster}，距现价 -{below_distance}

订单簿：买盘 {data['orderbook_bids']/1e6:.1f}M / 卖盘 {data['orderbook_asks']/1e6:.1f}M，失衡率 {data['orderbook_imbalance']:.4f}

持仓与情绪：
- 资金费率 {data['funding_rate']:.4f}%（7日分位 {data['funding_percentile']:.0f}%）
- OI {data['oi']/1e9:.2f}B（7日分位 {data['oi_percentile']:.0f}%），24h变化 {data['oi_change_24h']:+.1f}%
- 顶级交易员多空比 {data['top_ls_ratio']:.2f}（7日分位 {data['top_ls_percentile']:.0f}%）

资金流向：CVD斜率 {data['cvd_slope']:.4f}，期货24h净流 {data['netflow']/1e6:.1f}M USDT

---
请直接输出你的交易计划和分析。用口语化的专业语言，就像你在和另一个交易员讨论行情。

输出 JSON 格式（不要代码块）：
{{
  "direction": "long/short/neutral",
  "confidence": "high/medium/low",
  "position_size": "light/medium/heavy/none",
  "entry_price_low": 0.0,
  "entry_price_high": 0.0,
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "execution_plan": "一句话：方向、区间、止损、止盈、仓位、预计持仓时间。",
  "reasoning": "用口语化的段落叙述你的完整分析，包括你看重的数据和你的推演逻辑。",
  "risk_note": "最坏情况下的应对预案。"
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