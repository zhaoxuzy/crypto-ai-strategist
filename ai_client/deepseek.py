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

    prompt = f"""你是一位拥有十年经验的顶尖加密货币短线交易员，管理着200万U的自有资金。请基于以下数据，严格按照六个步骤进行深度推演。每一步必须包含“分析数据”和“做出结论”两个明确部分。

【数据面板 | {symbol} | {timestamp}】

价格：{current:.2f}
15min ATR：{data['atr_15m']:.2f} | 波动因子：{data['vol_factor']:.2f} | 7日价格分位数：{data['price_percentile']:.0f}%

清算池：
上方(空头)：{data['above_liq']/1e9:.2f}B，{above_cluster} (距{above_distance})
下方(多头)：{data['below_liq']/1e9:.2f}B，{below_cluster} (距{below_distance})
比值：{data['liq_ratio']:.3f}

订单簿：买盘 {data['orderbook_bids']/1e6:.1f}M / 卖盘 {data['orderbook_asks']/1e6:.1f}M | 失衡率 {data['orderbook_imbalance']:.4f}

持仓与情绪：
资金费率 {data['funding_rate']:.4f}% (7日分位{data['funding_percentile']:.0f}%)
OI {data['oi']/1e9:.2f}B (分位{data['oi_percentile']:.0f}%)，24h {data['oi_change_24h']:+.1f}%
全市场OI {data['agg_oi']/1e9:.2f}B，24h {data['agg_oi_change_24h']:+.1f}%
顶级多空比 {data['top_ls_ratio']:.2f} (分位{data['top_ls_percentile']:.0f}%)
恐慌贪婪：{data['fear_greed']} (7日前{data['fear_greed_prev_7d']})

期权：最大痛点 {data['max_pain']:.2f} | P/C比 {data['put_call_ratio']:.4f}

资金流：CVD斜率 {data['cvd_slope']:.4f} | 期货24h净流 {data['netflow']/1e6:.1f}M | 交易所BTC 24h变化 {data['exchange_btc_change_24h']:+.0f} BTC

跨市场：ETH/BTC {data['eth_btc_ratio']:.4f}

数据缺失：{missing_str}

---
请按以下六步进行推演，每一步都要用“第一步：环境定调”这样的标题，并明确分为“分析数据：”和“做出结论：”两部分：

第一步：环境定调
分析数据：价格7日分位数、15min ATR、波动因子。
做出结论：判断市场处于什么状态（高位/低位/中位？高波动/低波动？波动放大还是收敛？）。给出一个定性标签，并说明策略基调。

第二步：猎物定位
分析数据：上下方清算池的距离与强度、清算比值、订单簿买盘卖盘量、失衡率。
做出结论：对比上下方哪个池子更近、更脆、更容易被突破。判断大资金最可能去猎杀哪个方向。给出明确的方向倾向。

第三步：对手盘解剖
分析数据：OI分位数及变化、全市场OI变化、资金费率分位数、顶级多空比分位数、恐慌贪婪指数及趋势。
做出结论：判断市场拥挤度，找出谁的头寸最脆弱、谁可能成为反向燃料。给出犯错方判断。

第四步：资金流验证
分析数据：CVD斜率的方向和量级、期货24h净流、交易所BTC余额变化。
做出结论：判断资金流是否支持第三步的方向。三个资金指标是否共振？如果背离，矛盾在哪里？给出验证结果。

第五步：辅助信号扫描
分析数据：期权最大痛点、P/C比、ETH/BTC汇率。
做出结论：判断这些信号是加强还是削弱主逻辑。有没有隐藏风险？给出辅助判断。

第六步：矛盾裁决与决策
交叉验证与裁决：将前五步的结论放在一起比对。明确指出哪些信号互相印证、哪些彼此矛盾。如果存在矛盾，你必须给出明确的权重分配（例如：在当前高位环境下，我更信赖资金流的持续性，还是更警惕拥挤度的反转风险？为什么？）。最终形成一条主逻辑证据链。
推演与决策：
1. 基于主逻辑，推演价格最可能的运行路径。
2. 给出具体的入场区间，并**以入场区间的最差点（做多取上沿，做空取下沿）来计算最差盈亏比**。计算过程必须明确写出。
3. 止损位必须写明数据依据（如：放在哪个清算墙或结构位外侧，与ATR的关系）。
4. 止盈目标必须写明与流动性池或关键结构位的关系。
5. 仓位选择必须与证据链的强弱和矛盾程度挂钩。
6. **必须设定一个主动证伪信号**（例如：价格在X小时内无法突破Y位置，或CVD斜率开始走平），而不仅仅是被动止损。

注意：每一步都必须引用具体数据，不能泛泛而谈。所有提供的数据都要在你的推演中找到位置。

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
  "reasoning": "第一步：环境定调\\n分析数据：...\\n做出结论：...\\n\\n第二步：猎物定位\\n分析数据：...\\n做出结论：...\\n\\n第三步：对手盘解剖\\n分析数据：...\\n做出结论：...\\n\\n第四步：资金流验证\\n分析数据：...\\n做出结论：...\\n\\n第五步：辅助信号扫描\\n分析数据：...\\n做出结论：...\\n\\n第六步：矛盾裁决与决策\\n交叉验证与裁决：...\\n推演与决策：...",
  "risk_note": "风险说明，包含证伪信号和最坏情况预案。"
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
