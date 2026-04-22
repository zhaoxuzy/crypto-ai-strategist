import os
import json
import time
import re
from datetime import datetime
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
        parts = above_cluster.split('-')
        above_low = float(parts[0])
        above_high = float(parts[1])
        above_distance = f"+{above_high - current:.0f}"
    if below_cluster != 'N/A' and '-' in below_cluster:
        parts = below_cluster.split('-')
        below_low = float(parts[0])
        below_high = float(parts[1])
        below_distance = f"-{current - below_low:.0f}"

    data_quality = data.get("data_quality", {})
    missing = [k for k, v in data_quality.items() if v == "❌ 缺失"]
    missing_str = "、".join(missing) if missing else "无"

    max_pain = data['max_pain']
    max_pain_bias = "偏空信号（卖方对冲压制）" if current > max_pain else "偏多信号（卖方对冲支撑）"
    put_call_ratio = data['put_call_ratio']
    pc_bias = "偏空信号" if put_call_ratio > 1.0 else "偏多信号"

    prompt = f"""你是一个管理200万U的顶尖加密货币短线交易员。你的思维必须满足以下深度要求：
- 你从不接受表面的数据解读，你总是追问“这些数据背后，谁在亏钱？谁在赚钱？”
- 你在得出任何结论后，必须立即扮演反方，用最有力的证据攻击自己的结论。
- 只有当攻击失败时，你才暂时接受该结论，并明确写出它的脆弱前提。

请基于以下数据严格按六步推演。每一步必须包含“分析数据”、“第一反应”、“自我质疑”、“最终结论”四个环节。

【{symbol} | {timestamp}】

价格：{current:.2f} | 15min ATR：{data['atr_15m']:.2f} | 1h ATR：{data.get('atr_1h', data['atr_15m']*2):.2f} | 波动因子：{data['vol_factor']:.2f} | 7日分位数：{data['price_percentile']:.0f}%

清算池：
上方(空头)：{data['above_liq']/1e9:.2f}B，{above_cluster} (距{above_distance})
下方(多头)：{data['below_liq']/1e9:.2f}B，{below_cluster} (距{below_distance})
比值：{data['liq_ratio']:.3f}
注意：距离数值已计算好，直接引用，无需重新计算。清算集群区间已给出，请直接使用。

订单簿：买{data['orderbook_bids']/1e6:.1f}M / 卖{data['orderbook_asks']/1e6:.1f}M | 失衡率{data['orderbook_imbalance']:.4f}

持仓与情绪：
资金费率{data['funding_rate']:.4f}% (分位{data['funding_percentile']:.0f}%)
OI {data['oi']/1e9:.2f}B (分位{data['oi_percentile']:.0f}%)，24h{data['oi_change_24h']:+.1f}%
全市场OI {data['agg_oi']/1e9:.2f}B，24h{data['agg_oi_change_24h']:+.1f}%
顶级多空比{data['top_ls_ratio']:.2f} (分位{data['top_ls_percentile']:.0f}%)
恐慌贪婪：{data['fear_greed']} (7日前{data['fear_greed_prev_7d']})

期权信号硬规则（仅用于分析，盈亏比由系统计算）：
- 最大痛点{max_pain:.2f}（现价{current:.2f}）：判定为【{max_pain_bias}】。若现价>最大痛点，卖方对冲形成向下引力；若现价<最大痛点，形成向上引力。
- P/C比{put_call_ratio:.4f}：判定为【{pc_bias}】（>1.0偏空，<1.0偏多）。

资金流：CVD斜率{data['cvd_slope']:.4f} | 期货24h净流{data['netflow']/1e6:.1f}M | 交易所BTC 24h变化{data['exchange_btc_change_24h']:+.0f} BTC

跨市场：ETH/BTC {data['eth_btc_ratio']:.4f}

数据缺失：{missing_str}

---
第一步：环境定调
分析数据：价格7日分位数、15min ATR、1h ATR、波动因子。
第一反应：基于这些数据，最直接的市场状态判断是什么？
自我质疑：有什么数据或逻辑可能推翻这个判断？
最终结论：市场状态定性，并明确写出该结论成立的必要条件。

第二步：猎物定位
分析数据：上下方清算池距离/强度、比值、订单簿买卖盘量、失衡率。
第一反应：大资金最可能猎杀哪个方向的止损？
自我质疑：反向的清算池是否被忽视？订单簿的失衡是否可能是诱导性挂单？
最终结论：明确猎物方向，并指出最容易被假突破欺骗的情景。

第三步：对手盘解剖
分析数据：OI分位数及变化、全市场OI变化、资金费率分位数、顶级多空比分位数、恐慌贪婪及趋势。
特别规则：若资金费率分位 > 80% 且 CVD斜率 > 0.1 且价格分位数 > 90% 且波动因子 < 1.2，则拥挤度信号仅作为止盈参考。
第一反应：哪类头寸最脆弱？
自我质疑：脆弱头寸是否已经通过期权或现货进行了对冲？
最终结论：谁将成为燃料，并评估这种燃料的“燃烧效率”。

第四步：资金流验证
分析数据：CVD斜率方向/量级、期货24h净流、交易所BTC余额变化。
第一反应：资金流是否支持猎物方向？
自我质疑：CVD的斜率是否主要来自稳定币流入还是真实买盘？
最终结论：资金流与猎物方向的共振程度，并指出若出现何种具体数值变化则逻辑失效。

第五步：辅助信号扫描
分析数据：期权最大痛点、P/C比（严格参照硬规则解读）、ETH/BTC汇率。
第一反应：这些信号是加强还是削弱主逻辑？
自我质疑：最大痛点的引力是否会被Delta对冲抵消？
最终结论：辅助信号对主逻辑的净影响，并给出可信度权重。

第六步：矛盾裁决与决策
交叉验证与裁决：将前五步的最终结论并置，指出核心印证点与核心矛盾点。必须明确写出“如果我错了，最可能是因为哪一步的结论被推翻”。

推演与决策（注意：盈亏比和止损距离校验由系统自动完成，你只需提供点位和依据）：

【入场纪律与信号类型】
- 做多时，入场区间下限必须 ≥ 上方清算集群下沿（突破确认后入场）。
- 做空时，入场区间上限必须 ≤ 下方清算集群上沿。
- 若当前价格已满足入场条件，则 signal_type = "immediate"（即时信号）。
- 若当前价格未满足入场条件，但你判断价格将很快触发条件（例如先假突破再反转至入场区），你可以输出 signal_type = "pending"（挂单等待），并必须在 execution_plan 中说明等待的触发条件。
- 若两种情况都不值得出手，必须输出 signal_type = "neutral"。

1. 【价格路径推演 - 三段式】
   必须包含：① 每段的价格目标（精确到个位数）；② 触发该段运动的具体盘口信号；③ 预计持续时间。

2. 【入场区间】
   提供入场价格区间（下限和上限），并说明为何选择此区间。

3. 【止损位】
   必须给出具体价格，并明确写出依据的具体技术结构。

4. 【止盈目标】
   给出具体价格，并说明与哪个流动性池或结构相关。

5. 【赔率与胜率的定性权衡】
   根据逻辑强度，主观判断是否值得出手。若不值得，输出 neutral。

6. 【仓位选择】（light/medium/heavy）
   若存在显著矛盾信号，仓位至少降一级。

7. 【主动证伪信号】
   明确写出时间/价格/指标条件。

8. 【微观盘口确认】
   实盘200万U、滑点0.05%下，何种盘口细节会延迟3秒入场？

输出JSON（不要代码块）：
{{
  "signal_type": "immediate/pending/neutral",
  "direction": "long/short/neutral",
  "confidence": "high/medium/low (若direction为neutral，此项必须为low)",
  "position_size": "light/medium/heavy/none",
  "entry_price_low": 0.0,
  "entry_price_high": 0.0,
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "execution_plan": "一句话指令。对于pending信号，必须写明触发条件。",
  "reasoning": "（完整的六步推演）",
  "risk_note": "风险说明，含证伪信号和最坏情况预案。"
}}
"""
    return prompt


def _log_response_to_file(prompt: str, content: str, reasoning_content: str = None):
    try:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{log_dir}/deepseek_response_{timestamp}.json"
        record = {
            "timestamp": timestamp,
            "prompt": prompt,
            "content": content,
            "reasoning_content": reasoning_content
        }
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        logger.info(f"响应已保存至 {filename}")
    except Exception as e:
        logger.warning(f"保存响应日志失败: {e}")


def extract_json_from_content(content: str) -> str:
    match = re.search(r'```json\s*([\s\S]*?)\s*```', content)
    if match:
        return match.group(1).strip()
    match = re.search(r'```\s*([\s\S]*?)\s*```', content)
    if match:
        return match.group(1).strip()
    start = content.find('{')
    if start == -1:
        raise ValueError("未找到 JSON 起始花括号")
    brace_count = 0
    in_string = False
    escape = False
    for i in range(start, len(content)):
        c = content[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            brace_count += 1
        elif c == '}':
            brace_count -= 1
            if brace_count == 0:
                return content[start:i+1].strip()
    raise ValueError("未找到匹配的结束花括号")


def call_deepseek(prompt: str, max_retries: int = 3) -> dict:
    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1",
        timeout=180.0
    )

    for attempt in range(max_retries):
        try:
            logger.info(f"DeepSeek Reasoner API 调用 (尝试 {attempt+1}/{max_retries})")
            resp = client.chat.completions.create(
                model="deepseek-reasoner",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=6000,
                timeout=180
            )
            content = resp.choices[0].message.content or ""
            finish_reason = resp.choices[0].finish_reason
            reasoning = getattr(resp.choices[0].message, 'reasoning_content', None)

            logger.info(f"finish_reason: {finish_reason}, content长度: {len(content)}")

            _log_response_to_file(prompt, content, reasoning)

            if not content.strip():
                raise ValueError("模型返回空 content")

            json_str = extract_json_from_content(content)
            s = json.loads(json_str)

            s.setdefault("signal_type", "neutral")
            s.setdefault("position_size", "none")
            s.setdefault("execution_plan", "")
            s.setdefault("risk_note", "")
            return s

        except Exception as e:
            logger.warning(f"调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                logger.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                raise
    return {}


def validate_strategy(s: dict, data: dict = None) -> tuple[bool, str]:
    """
    校验策略输出，根据 signal_type 执行不同校验规则。
    """
    signal_type = s.get("signal_type", "neutral")
    direction = s.get("direction", "neutral")

    # neutral 处理
    if signal_type == "neutral" or direction == "neutral":
        if s.get("confidence") not in ["low"]:
            s["confidence"] = "low"
        entry_low = s.get("entry_price_low", 0)
        entry_high = s.get("entry_price_high", 0)
        stop = s.get("stop_loss", 0)
        tp = s.get("take_profit", 0)
        if entry_low > 0 or entry_high > 0 or stop > 0 or tp > 0:
            return False, "方向为 neutral 但提供了非零价格计划"
        return True, ""

    if direction not in ["long", "short"]:
        return False, f"无效方向: {direction}"

    try:
        entry_low = float(s.get("entry_price_low", 0))
        entry_high = float(s.get("entry_price_high", 0))
        stop = float(s.get("stop_loss", 0))
        tp = float(s.get("take_profit", 0))
        if entry_low <= 0 or entry_high <= 0 or stop <= 0 or tp <= 0:
            return False, "价格必须为正数"
        if entry_low > entry_high:
            return False, "入场区间下限大于上限"

        reasoning = s.get("reasoning", "")
        atr_1h = data.get("atr_1h", data.get("atr_15m", 0) * 2) if data else 0
        current_price = data.get("mark_price", 0) if data else 0

        # 1. 入场纪律检查（所有信号必须满足）
        if data:
            above_cluster = data.get("above_cluster", "")
            below_cluster = data.get("below_cluster", "")
            if direction == "long" and above_cluster and '-' in above_cluster:
                above_low = float(above_cluster.split('-')[0])
                if entry_low < above_low:
                    return False, f"做多入场下限{entry_low}低于上方清算集群下沿{above_low}"
            elif direction == "short" and below_cluster and '-' in below_cluster:
                below_high = float(below_cluster.split('-')[1])
                if entry_high > below_high:
                    return False, f"做空入场上限{entry_high}高于下方清算集群上沿{below_high}"

        # 2. 根据信号类型执行现价距离校验
        if signal_type == "immediate":
            if current_price > 0:
                if direction == "long" and entry_low < current_price:
                    return False, f"即时做多信号要求入场下限≥现价，但{entry_low} < {current_price}"
                if direction == "short" and entry_high > current_price:
                    return False, f"即时做空信号要求入场上限≤现价，但{entry_high} > {current_price}"
        elif signal_type == "pending":
            if current_price > 0:
                if direction == "long" and entry_low >= current_price:
                    logger.warning(f"挂单做多信号但入场下限{entry_low}≥现价{current_price}，可能应为即时信号")
                if direction == "short" and entry_high <= current_price:
                    logger.warning(f"挂单做空信号但入场上限{entry_high}≤现价{current_price}，可能应为即时信号")
            # 检查 execution_plan 是否包含触发条件
            exec_plan = s.get("execution_plan", "")
            if "触发" not in exec_plan and "等待" not in exec_plan:
                logger.warning("pending 信号的 execution_plan 缺少触发条件描述")
        else:
            return False, f"无效的 signal_type: {signal_type}"

        # 3. 止损距离检查
        if atr_1h > 0:
            worst_entry = entry_high if direction == "long" else entry_low
            stop_distance = abs(worst_entry - stop)
            if stop_distance < 1.2 * atr_1h:
                return False, f"止损距离{stop_distance:.2f}小于1.2倍1h ATR({1.2*atr_1h:.2f})"

        # 4. 盈亏比计算与校验
        MIN_RR = 1.5
        if direction == "long":
            worst_entry = entry_high
            if worst_entry >= tp or worst_entry <= stop:
                return False, "止损/止盈与入场价关系错误"
            risk = worst_entry - stop
            reward = tp - worst_entry
        else:
            worst_entry = entry_low
            if worst_entry <= tp or worst_entry >= stop:
                return False, "止损/止盈与入场价关系错误"
            risk = stop - worst_entry
            reward = worst_entry - tp

        rr = reward / risk if risk > 0 else 0
        if rr < MIN_RR:
            return False, f"盈亏比{rr:.2f}低于最低要求{MIN_RR}"

        s["_calculated_rr"] = round(rr, 2)
        s["_calculated_risk"] = round(risk, 2)
        s["_calculated_reward"] = round(reward, 2)

        # 5. 深度思考痕迹检查
        if "自我质疑" not in reasoning:
            logger.warning("策略校验警告：reasoning中缺少'自我质疑'环节")
        if "如果我错了" not in reasoning:
            logger.warning("策略校验警告：reasoning中缺少'如果我错了'的反思陈述")

        # 6. 仓位与矛盾信号联动
        contradiction_keywords = ["矛盾", "背离", "冲突", "不一致"]
        if any(kw in reasoning for kw in contradiction_keywords):
            if s.get("position_size") == "heavy":
                logger.warning("存在矛盾信号但仓位为heavy，已强制降级为medium")
                s["position_size"] = "medium"

        return True, ""

    except Exception as e:
        return False, f"数值解析失败: {e}"
