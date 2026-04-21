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

    # 计算清算目标距离
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
核心信条：不扛单，不格局，吃一口流动性就跑。
语言风格：直接、冷血、关注具体点位和盈亏比，杜绝宏观分析。

【市场数据快照 | {timestamp} | 标的: {symbol}】

### ⚠️ 数据完整性
{quality_table}

### 1. 流动性池子
| 方向 | 累计清算强度 | 最近密集区 | 距现价 |
|------|-------------|-----------|--------|
| 上方(空头) | {data['above_liq']/1e9:.2f}B | {above_cluster} | +{above_distance} |
| 下方(多头) | {data['below_liq']/1e9:.2f}B | {below_cluster} | -{below_distance} |
| 上方/下方比 | {data['liq_ratio']:.3f} | — | — |
| 订单簿买盘 | {data['orderbook_bids']/1e6:.1f}M | 卖盘 | {data['orderbook_asks']/1e6:.1f}M |
| 订单簿失衡率 | {data['orderbook_imbalance']:.4f} | — | — |

### 2. 主动成交脉搏
| 指标 | 值 |
|------|------|
| CVD 4h均值 | {data['cvd_mean']:.2f} M USDT |
| CVD 4h斜率 | {data['cvd_slope']:.4f} |
| 期货资金净流(24h) | {data['netflow']/1e6:.1f}M USDT |

### 3. 拥挤度与燃料
| 指标 | 值 | 7日分位数 |
|------|------|-----------|
| 加权资金费率 | {data['funding_rate']:.4f}% | {data['funding_percentile']:.1f}% |
| 持仓量(OI) | {data['oi']/1e9:.2f}B | {data['oi_percentile']:.1f}% |
| OI 24h变化 | {data['oi_change_24h']:+.1f}% | — |
| 顶级交易员多空比 | {data['top_ls_ratio']:.2f} | {data['top_ls_percentile']:.1f}% |

### 4. 盈亏比参数
| 指标 | 值 |
|------|------|
| 当前价格 | {current:.2f} |
| 15分钟 ATR | {data['atr_15m']:.2f} |
| Put/Call Ratio | {data['put_call_ratio']:.4f} |
| 恐慌贪婪指数 | {data['fear_greed']} (7日前:{data['fear_greed_prev_7d']}) |

---
# 短线猎杀六步推演（必须严格遵循，每步引用具体数值）

**1. 定位流动性池子**
上方池子在哪？下方池子在哪？哪个更近、更薄、更容易被吃掉？引用具体价位和强度。

**2. 读取主动成交脉搏**
CVD斜率是陡峭向上、向下还是走平？近期成交量是放大还是萎缩？是否有聪明钱在吸筹或派发？引用具体斜率值。

**3. 评估拥挤度与燃料**
资金费率处于什么分位数？OI是加速增长还是萎缩？现在追多/追空是否会成为对手盘的燃料？引用具体分位数。

**4. 计算非对称盈亏比**
如果做多：止损设在哪？（通常为下方清算墙外侧）止盈看哪？（上方清算池前）。用15分钟ATR验证止损是否合理。盈亏比是否≥2:1？
如果做空：止损设在哪？（上方清算墙外侧）止盈看哪？（下方清算池前）。盈亏比是否≥2:1？

**5. 制定进场与止损计划**
基于以上分析，选择做多、做空或观望。给出具体入场区间、止损价位、仓位。止损必须有数据依据（清算墙外侧或关键位外）。

**6. 设计离场与滑点控制**
到达目标位后，是挂限价单还是主动市价出货？若触发清算瀑布如何应对？反面情景预案是什么？

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
  "reasoning": "【步骤1】...\\n【步骤2】...\\n【步骤3】...\\n【步骤4】...\\n【步骤5】...\\n【步骤6】...",
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
