import os
import json
from openai import OpenAI
from utils.logger import logger


def build_prompt(data: dict, symbol: str) -> str:
    timestamp = data.get("timestamp", "N/A")
    data_quality = data.get("data_quality", {})

    # 构建数据完整性表格
    quality_rows = []
    for key, status in data_quality.items():
        quality_rows.append(f"| {key} | {status} |")
    quality_table = "\n".join(quality_rows) if quality_rows else "| - | - |"

    prompt = f"""你是一个管理千万美元的对冲基金加密货币交易员。请仅基于以下表格中的原始数据，严格按六步框架完成一次完整的交易决策推理。

【市场数据快照 | {timestamp} | 决策周期: 4h | 标的: {symbol}】

### ⚠️ 数据完整性声明
| 指标 | 状态 |
|------|------|
{quality_table}
> 若核心指标（清算、CVD、资金费率）缺失 ≥2 项，请在推理中降低置信度并说明。

### 1. 价格与波动
| 指标 | 值 | 单位 | 7日分位数 |
|------|------|------|-----------|
| 标记价格 | {data['mark_price']:.2f} | USDT | {data['price_percentile']:.1f}% |
| 4h ATR | {data['atr']:.2f} | USDT | — |
| 波动因子 | {data['vol_factor']:.2f} | 比值 | — |

### 2. 清算压力分布
| 方向 | 累计清算强度 | 单位 | 最近密集区价格 |
|------|-------------|------|---------------|
| 上方(空头清算) | {data['above_liq']/1e9:.2f}B | USDT | {data['above_cluster']} |
| 下方(多头清算) | {data['below_liq']/1e9:.2f}B | USDT | {data['below_cluster']} |
| **上方/下方比** | **{data['liq_ratio']:.3f}** | 比值 | — |
| 期权最大痛点 | {data['max_pain']:.2f} | USDT | — |

### 3. 微观结构
| 指标 | 值 | 单位 |
|------|------|------|
| 订单簿买盘总量 | {data['orderbook_bids']/1e6:.2f}M | USDT |
| 订单簿卖盘总量 | {data['orderbook_asks']/1e6:.2f}M | USDT |
| 订单簿失衡率 | {data['orderbook_imbalance']:.4f} | — |
| 期货资金净流向(24h) | {data['netflow']/1e6:.2f}M | USDT |

### 4. 多空博弈
| 指标 | 值 | 单位 | 7日分位数 |
|------|------|------|-----------|
| 顶级交易员多空比 | {data['top_ls_ratio']:.2f} | 比值 | {data['top_ls_percentile']:.1f}% |
| 加权资金费率 | {data['funding_rate']:.4f} | % | {data['funding_percentile']:.1f}% |
| 持仓量(OI) | {data['oi']/1e9:.2f}B | USDT | {data['oi_percentile']:.1f}% |
| OI 24h变化 | {data['oi_change_24h']:+.1f} | % | — |
| 全市场OI | {data['agg_oi']/1e9:.2f}B | USDT | — |
| 全市场OI 24h变化 | {data['agg_oi_change_24h']:+.1f} | % | — |
| 交易所BTC总量 | {data['exchange_btc_total']/1e6:.2f}M | BTC | — |
| 交易所BTC 24h变化 | {data['exchange_btc_change_24h']:+.1f} | % | — |

### 5. 资金流向
| 指标 | 值 | 单位 |
|------|------|------|
| CVD 4h均值 | {data['cvd_mean']:.2f} | M USDT |
| CVD 4h斜率 | {data['cvd_slope']:.4f} | — |

### 6. 宏观与期权
| 指标 | 值 | 单位 |
|------|------|------|
| 恐慌贪婪指数 | {data['fear_greed']} | 0-100 |
| 恐慌贪婪指数(7日前) | {data['fear_greed_prev_7d']} | 0-100 |
| ETH/BTC 汇率 | {data['eth_btc_ratio']:.4f} | 比值 |

---
# 分析框架（必须严格遵循以下六步进行推理，不可跳过任何一步）

1. **市场状态识别**：基于价格动量、波动因子和ATR，判断当前市场是趋势市还是震荡市。引用具体数值。
2. **流动性动力学分析**：根据清算压力分布，判断大资金可能推动价格去"猎杀"哪个区域。引用具体数值。
3. **微观结构验证**：分析订单簿失衡率和期货资金净流向，判断微观层面的买卖力量对比。引用具体数值。
4. **资金与情绪博弈**：分析资金费率分位数、顶级交易员多空比、持仓量变化、恐惧贪婪趋势、交易所BTC余额变化。特别注意极端拥挤风险。引用具体数值。
5. **资金流向验证**：分析CVD的方向与斜率，验证前几步的判断是否得到资金面支持。引用具体数值。
6. **综合研判与风控**：生成最终交易策略，明确方向、置信度、仓位、入场区间、止损、止盈。必须包含反面情景预案（什么条件下策略失效）。每项风控参数必须说明数据依据。

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
