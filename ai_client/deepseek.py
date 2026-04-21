import os
import json
from openai import OpenAI
from utils.logger import logger


def build_prompt(data: dict, symbol: str) -> str:
    timestamp = data.get("timestamp", "N/A")
    data_quality = data.get("data_quality", {})

    quality_rows = []
    for key, status in data_quality.items():
        quality_rows.append(f"| {key} | {status} |")
    quality_table = "\n".join(quality_rows) if quality_rows else "| - | - |"

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

    prompt = f"""你是一名操作 100万-500万 U 资金的顶级加密货币合约短线交易员。
核心信条：不扛单，不格局，吃一口流动性就跑。每一笔交易必须有明确的猎物和止损理由。

【市场数据快照 | {timestamp} | 标的: {symbol}】

### ⚠️ 数据完整性
{quality_table}

### 1. 猎物在哪（流动性池子）
| 方向 | 累计清算强度 | 最近密集区 | 距现价 |
|------|-------------|-----------|--------|
| 上方(空头) | {data['above_liq']/1e9:.2f}B | {above_cluster} | +{above_distance} |
| 下方(多头) | {data['below_liq']/1e9:.2f}B | {below_cluster} | -{below_distance} |
| 订单簿买盘 | {data['orderbook_bids']/1e6:.1f}M | 卖盘 | {data['orderbook_asks']/1e6:.1f}M |
| 失衡率 | {data['orderbook_imbalance']:.4f} | — | — |

### 2. 陷阱在哪（拥挤度与燃料）
| 指标 | 值 | 7日分位数 |
|------|------|-----------|
| 加权资金费率 | {data['funding_rate']:.4f}% | {data['funding_percentile']:.1f}% |
| 持仓量(OI) | {data['oi']/1e9:.2f}B | {data['oi_percentile']:.1f}% |
| OI 24h变化 | {data['oi_change_24h']:+.1f}% | — |
| 顶级交易员多空比 | {data['top_ls_ratio']:.2f} | {data['top_ls_percentile']:.1f}% |

### 3. 风向在哪（主动成交与资金流）
| 指标 | 值 |
|------|------|
| CVD 4h斜率 | {data['cvd_slope']:.4f} |
| 期货资金净流(24h) | {data['netflow']/1e6:.1f}M USDT |
| 当前价格 | {current:.2f} |
| 15分钟 ATR | {data['atr_15m']:.2f} |

---
# 短线猎杀六步推演（必须连贯推理，严禁数据罗列）

**第一步：哪个猎物值得打？**
对比上下两个清算池的距离、强度和订单簿厚度。指出哪个方向的池子更近、更薄、更容易被快速吃掉。如果上下池子距离相当或都太远（超过1.5倍ATR），直接给出观望结论。引用具体价位和距离。

**第二步：现在冲进去会不会成燃料？**
检查资金费率分位数和OI增长速度。如果费率极端（>80%分位）且OI还在猛增，说明同方向已经拥挤不堪，冲进去就是给对手送燃料。结合顶级交易员持仓方向，判断对手盘是谁。

**第三步：主动成交在帮谁？**
CVD斜率是朝哪个方向倾斜？斜率陡峭还是走平？如果CVD方向与猎物方向一致，说明聪明钱在铺路；如果相反，说明可能有陷阱。资金净流是流入还是流出？验证风向是否真实。

**第四步：止损放哪才不会被扫？**
短线止损不是随便设个2%，必须放在猎物池子外侧（做多时放在下方清算墙下方，做空时放在上方清算墙上方）。用15分钟ATR验证：止损距离应大于1.5倍ATR，否则容易被噪音扫掉。如果止损距离超过止盈距离的一半，这笔单盈亏比必然拉胯。

**第五步：盈亏比够不够扣动扳机？**
计算：止盈距离 = 目标池子边缘 - 当前价；止损距离 = 当前价 - 止损位。盈亏比 = 止盈距离 / 止损距离。如果 < 2:1，除非有极强盘口信号（如CVD陡峭+订单簿薄如纸），否则不开枪。给出明确计算过程。

**第六步：怎么吃怎么跑？**
进场：是在当前价直接进，还是挂单等回调？仓位轻中重？
离场：到目标位是挂限价单（流动性足够时）还是主动市价砸（防止滑点）？如果价格打到目标池子边缘却迟迟不破，是否主动止盈？如果触发止损，是市价无条件离场还是有其他预案？

---
【输出格式】严格JSON，不要用```json```包裹。

{{
  "direction": "long" / "short" / "neutral",
  "confidence": "high" / "medium" / "low",
  "position_size": "light" / "medium" / "heavy" / "none",
  "entry_price_low": 0.0,
  "entry_price_high": 0.0,
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "execution_plan": "极简指令：做多/做空，进场区间，止损，止盈，预计持仓时间，离场方式。不超过80字。",
  "reasoning": "【第一步】...\\n【第二步】...\\n【第三步】...\\n【第四步】...\\n【第五步】...\\n【第六步】...",
  "risk_note": "主要风险及反面情景预案"
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