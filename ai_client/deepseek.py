import os
import json
import time
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

    prompt = f"""你是拥有十年经验的顶尖加密货币短线交易员，管理200万U资金。请基于以下数据严格按六步推演，每步包含“分析数据”和“做出结论”。

【{symbol} | {timestamp}】

价格：{current:.2f} | 15min ATR：{data['atr_15m']:.2f} | 1h ATR：{data.get('atr_1h', data['atr_15m']*2):.2f} | 波动因子：{data['vol_factor']:.2f} | 7日分位数：{data['price_percentile']:.0f}%

清算池：
上方(空头)：{data['above_liq']/1e9:.2f}B，{above_cluster} (距{above_distance})
下方(多头)：{data['below_liq']/1e9:.2f}B，{below_cluster} (距{below_distance})
比值：{data['liq_ratio']:.3f}

订单簿：买{data['orderbook_bids']/1e6:.1f}M / 卖{data['orderbook_asks']/1e6:.1f}M | 失衡率{data['orderbook_imbalance']:.4f}

持仓与情绪：
资金费率{data['funding_rate']:.4f}% (分位{data['funding_percentile']:.0f}%)
OI {data['oi']/1e9:.2f}B (分位{data['oi_percentile']:.0f}%)，24h{data['oi_change_24h']:+.1f}%
全市场OI {data['agg_oi']/1e9:.2f}B，24h{data['agg_oi_change_24h']:+.1f}%
顶级多空比{data['top_ls_ratio']:.2f} (分位{data['top_ls_percentile']:.0f}%)
恐慌贪婪：{data['fear_greed']} (7日前{data['fear_greed_prev_7d']})

期权：最大痛点{data['max_pain']:.2f} | P/C比{data['put_call_ratio']:.4f}

资金流：CVD斜率{data['cvd_slope']:.4f} | 期货24h净流{data['netflow']/1e6:.1f}M | 交易所BTC 24h变化{data['exchange_btc_change_24h']:+.0f} BTC

跨市场：ETH/BTC {data['eth_btc_ratio']:.4f}

数据缺失：{missing_str}

---
第一步：环境定调
分析数据：价格7日分位数、15min ATR、1h ATR、波动因子。
做出结论：市场状态定性（高位/低位、波动放大/收敛），策略基调。

第二步：猎物定位
分析数据：上下方清算池距离/强度、比值、订单簿买卖盘量、失衡率。
做出结论：哪个方向池子更近更脆，大资金最可能猎杀方向。

第三步：对手盘解剖
分析数据：OI分位数及变化、全市场OI变化、资金费率分位数、顶级多空比分位数、恐慌贪婪及趋势。
特别规则：若资金费率分位 > 80% 且 CVD斜率 > 0.1 且价格未跌破15min EMA12，则拥挤度信号仅作为止盈参考，不作为反转开仓依据。
做出结论：市场拥挤度，谁头寸脆弱、谁可能成为燃料。

第四步：资金流验证
分析数据：CVD斜率方向/量级、期货24h净流、交易所BTC余额变化。
做出结论：资金流是否支持猎物方向，三个指标是否共振或背离。

第五步：辅助信号扫描
分析数据：期权最大痛点、P/C比、ETH/BTC汇率。
做出结论：这些信号是加强还是削弱主逻辑，有无隐藏风险。

第六步：矛盾裁决与决策
交叉验证与裁决：比对前五步结论，指出印证与矛盾点，明确权重分配，形成主逻辑。
推演与决策：
1. 推演价格最可能路径。
2. 入场区间。以入场区间最差点（做多取上沿，做空取下沿）作为成本，写出完整的盈亏比计算公式：`|止损 - 最差入场| : |止盈 - 最差入场|`。
3. 止损位及数据依据。止损必须同时满足：① 在关键清算墙或结构位外侧；② 距离 ≥ 1.2倍 1小时 ATR。取两者较大值。
4. 止盈目标与流动性池关系。
5. 赔率与胜率的权衡：基于上述盈亏比，结合证据链强弱，独立判断期望值是否为正。若赔率较低但你认为胜率极高，必须明确写出论证。若不值得出手，输出neutral并解释。
6. 仓位选择与证据链强弱挂钩。
7. 主动证伪信号（时间/价格/指标条件）。
8. 微观盘口确认：假如你此刻必须用实盘200万U执行此计划，且滑点设置为0.05%，你会因为什么具体的微观盘口细节（例如卖一挂单厚度）而延迟3秒入场？

输出JSON（不要代码块）：
{{
  "direction": "long/short/neutral",
  "confidence": "high/medium/low",
  "position_size": "light/medium/heavy/none",
  "entry_price_low": 0.0,
  "entry_price_high": 0.0,
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "execution_plan": "一句话指令。",
  "reasoning": "第一步：环境定调\\n分析数据：...\\n做出结论：...\\n\\n第二步：...",
  "risk_note": "风险说明，含证伪信号和最坏情况预案。"
}}
"""
    return prompt


def call_deepseek(prompt: str, max_retries: int = 3) -> dict:
    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1",
        timeout=120.0
    )
    
    for attempt in range(max_retries):
        try:
            logger.info(f"DeepSeek Reasoner API 调用 (尝试 {attempt+1}/{max_retries})，Prompt 长度: {len(prompt)} 字符")
            resp = client.chat.completions.create(
                model="deepseek-reasoner",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000,
                timeout=120
            )
            content = resp.choices[0].message.content
            logger.info(f"DeepSeek Reasoner 响应成功，内容长度: {len(content)}")

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
                raise ValueError("无法提取 JSON")

            s = json.loads(json_str)
            s.setdefault("position_size", "none")
            s.setdefault("execution_plan", "")
            s.setdefault("risk_note", "")
            return s

        except Exception as e:
            logger.warning(f"DeepSeek Reasoner 调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                logger.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                raise

    return {}


def validate_strategy(s: dict) -> tuple[bool, str]:
    direction = s.get("direction")
    if direction not in ["long", "short", "neutral"]:
        return False, f"无效方向: {direction}"
    
    # 增强校验：neutral 方向时，入场/止损/止盈必须全部为 0 或不存在
    if direction == "neutral":
        entry_low = s.get("entry_price_low", 0)
        entry_high = s.get("entry_price_high", 0)
        stop = s.get("stop_loss", 0)
        tp = s.get("take_profit", 0)
        if entry_low > 0 or entry_high > 0 or stop > 0 or tp > 0:
            return False, "方向为 neutral 但提供了非零价格计划，存在幻觉风险"
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
