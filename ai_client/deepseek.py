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

    # 期权硬规则提示
    max_pain = data['max_pain']
    max_pain_bias = "偏空信号（卖方对冲压制）" if current > max_pain else "偏多信号（卖方对冲支撑）"
    put_call_ratio = data['put_call_ratio']
    pc_bias = "偏空信号" if put_call_ratio > 1.0 else "偏多信号"

    # 注意：移除了对15min EMA12的依赖，改用波动因子和价格分位数作为替代
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

期权信号硬规则：
- 最大痛点{max_pain:.2f}（现价{current:.2f}）：判定为【{max_pain_bias}】。若现价>最大痛点，卖方对冲形成向下引力；若现价<最大痛点，形成向上引力。
- P/C比{put_call_ratio:.4f}：判定为【{pc_bias}】（>1.0偏空，<1.0偏多）。

资金流：CVD斜率{data['cvd_slope']:.4f} | 期货24h净流{data['netflow']/1e6:.1f}M | 交易所BTC 24h变化{data['exchange_btc_change_24h']:+.0f} BTC

跨市场：ETH/BTC {data['eth_btc_ratio']:.4f}

数据缺失：{missing_str}

---
第一步：环境定调
分析数据：价格7日分位数、15min ATR、1h ATR、波动因子。
第一反应：基于这些数据，最直接的市场状态判断是什么？
自我质疑：有什么数据或逻辑可能推翻这个判断？（例如：高波动因子可能只是瞬时毛刺？高位分位数是否伴随成交量萎缩？）
最终结论：市场状态定性，并明确写出该结论成立的必要条件。

第二步：猎物定位
分析数据：上下方清算池距离/强度、比值、订单簿买卖盘量、失衡率。
第一反应：大资金最可能猎杀哪个方向的止损？
自我质疑：反向的清算池是否被忽视？订单簿的失衡是否可能是诱导性挂单？
最终结论：明确猎物方向，并指出最容易被假突破欺骗的情景。

第三步：对手盘解剖
分析数据：OI分位数及变化、全市场OI变化、资金费率分位数、顶级多空比分位数、恐慌贪婪及趋势。
特别规则：若资金费率分位 > 80% 且 CVD斜率 > 0.1 且价格分位数 > 90% 且波动因子 < 1.2，则拥挤度信号仅作为止盈参考（注：原EMA12条件已替换为现有指标）。
第一反应：哪类头寸最脆弱？
自我质疑：脆弱头寸是否已经通过期权或现货进行了对冲？
最终结论：谁将成为燃料，并评估这种燃料的“燃烧效率”。

第四步：资金流验证
分析数据：CVD斜率方向/量级、期货24h净流、交易所BTC余额变化。
第一反应：资金流是否支持猎物方向？
自我质疑：CVD的斜率是否主要来自稳定币流入还是真实买盘？交易所余额变化是否被单一巨鲸地址干扰？
最终结论：资金流与猎物方向的共振程度，并指出若出现何种具体数值变化则逻辑失效。

第五步：辅助信号扫描
分析数据：期权最大痛点、P/C比（严格参照硬规则解读）、ETH/BTC汇率。
第一反应：这些信号是加强还是削弱主逻辑？
自我质疑：最大痛点的引力是否会在到期前被Delta对冲力量抵消？P/C比是否因为大户卖出看跌期权而失真？
最终结论：辅助信号对主逻辑的净影响，并给出可信度权重。

第六步：矛盾裁决与决策
交叉验证与裁决：将前五步的最终结论并置，指出核心印证点与核心矛盾点。必须明确写出“如果我错了，最可能是因为哪一步的结论被推翻”。
推演与决策：
【入场纪律】
- 做多时，入场区间下限必须 ≥ 上方清算集群下沿（突破确认后入场）。
- 做空时，入场区间上限必须 ≤ 下方清算集群上沿。
若无法满足，必须输出neutral。

1. 【价格路径推演 - 三段式】
   用“先……然后……最后……”描述完整演变，必须包含具体价格点位和触发条件。

2. 【盈亏比唯一计算】
   定义：最差入场价 = 做多时的入场区间上沿，做空时的入场区间下沿。
   输出格式：`盈亏比 = (止盈 - 最差入场) / (最差入场 - 止损)`，并在reasoning中输出该数值。

3. 止损位及数据依据：① 在关键清算墙或结构位外侧；② 距离 ≥ 1.2倍 1小时 ATR。取两者较大值。

4. 止盈目标与流动性池关系。

5. 赔率与胜率的权衡：若不值得出手，输出neutral并解释。

6. 仓位选择（light/medium/heavy）。若存在显著矛盾信号，仓位至少降一级。

7. 主动证伪信号（时间/价格/指标条件）。

8. 微观盘口确认：实盘200万U、滑点0.05%下，何种盘口细节会延迟3秒入场？

输出JSON（不要代码块）：
{{
  "direction": "long/short/neutral",
  "confidence": "high/medium/low (注意：若direction为neutral，此项必须为low)",
  "position_size": "light/medium/heavy/none",
  "entry_price_low": 0.0,
  "entry_price_high": 0.0,
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "execution_plan": "一句话指令。",
  "reasoning": "第一步：环境定调\\n分析数据：...\\n第一反应：...\\n自我质疑：...\\n最终结论：...\\n\\n第二步：...\\n\\n第六步：矛盾裁决与决策\\n交叉验证与裁决：...\\n如果我错了，最可能是因为：...\\n推演与决策：\\n1. 价格路径推演：...\\n2. 盈亏比计算：...\\n3. 止损位：...\\n4. 止盈目标：...\\n5. 赔率与胜率权衡：...\\n6. 仓位选择：...\\n7. 主动证伪信号：...\\n8. 微观盘口确认：...",
  "risk_note": "风险说明，含证伪信号和最坏情况预案。"
}}
"""
    return prompt


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
                max_tokens=4000,
                timeout=180
            )
            content = resp.choices[0].message.content
            
            # 记录推理内容（如果有）
            reasoning = getattr(resp.choices[0].message, 'reasoning_content', None)
            _log_response_to_file(prompt, content, reasoning)
            
            logger.info(f"响应成功，内容长度: {len(content)}")
            
            # 鲁棒提取 JSON
            json_str = extract_json_from_content(content)
            s = json.loads(json_str)
            
            s.setdefault("position_size", "none")
            s.setdefault("execution_plan", "")
            s.setdefault("risk_note", "")
            return s
            
        except Exception as e:
            logger.warning(f"调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                raise
    return {}


def _log_response_to_file(prompt: str, content: str, reasoning_content: str = None):
    """将响应持久化到日志文件"""
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


def validate_strategy(s: dict, data: dict = None) -> tuple[bool, str]:
    """
    校验策略输出，若关键规则违反则返回False。
    """
    direction = s.get("direction")
    if direction not in ["long", "short", "neutral"]:
        return False, f"无效方向: {direction}"
    
    # neutral 时强制 confidence 为 low，且价格必须全0
    if direction == "neutral":
        if s.get("confidence") not in ["low"]:
            logger.warning("策略校验警告：direction为neutral，但confidence非low，已强制设为low")
            s["confidence"] = "low"
        entry_low = s.get("entry_price_low", 0)
        entry_high = s.get("entry_price_high", 0)
        stop = s.get("stop_loss", 0)
        tp = s.get("take_profit", 0)
        if entry_low > 0 or entry_high > 0 or stop > 0 or tp > 0:
            return False, "方向为 neutral 但提供了非零价格计划，存在幻觉风险"
        # 检查是否包含推演痕迹
        reasoning = s.get("reasoning", "")
        if "自我质疑" not in reasoning:
            logger.warning("策略校验警告：reasoning中缺少'自我质疑'环节")
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

        reasoning = s.get("reasoning", "")
        atr_1h = data.get("atr_1h", data.get("atr_15m", 0) * 2) if data else 0

        # 1. 盈亏比一致性检查（强制阻断）
        rr_match = re.search(r'盈亏比[＝=:：]\s*(\d+\.?\d*)', reasoning)
        if rr_match:
            claimed_rr = float(rr_match.group(1))
            if direction == "long":
                worst_entry = entry_high
                actual_rr = round((tp - worst_entry) / (worst_entry - stop), 2) if worst_entry != stop else 0
            else:
                worst_entry = entry_low
                actual_rr = round((worst_entry - tp) / (stop - worst_entry), 2) if stop != worst_entry else 0
            if abs(claimed_rr - actual_rr) > 0.1:
                return False, f"盈亏比计算矛盾: 声称{claimed_rr} vs 实际{actual_rr}"

        # 2. 入场纪律检查（强制阻断）
        if data:
            # 解析清算集群区间
            above_cluster = data.get("above_cluster", "")
            below_cluster = data.get("below_cluster", "")
            if direction == "long" and above_cluster and '-' in above_cluster:
                above_low = float(above_cluster.split('-')[0])
                if entry_low < above_low:
                    return False, f"做多入场下限{entry_low}低于上方清算集群下沿{above_low}，违反入场纪律"
            elif direction == "short" and below_cluster and '-' in below_cluster:
                below_high = float(below_cluster.split('-')[1])
                if entry_high > below_high:
                    return False, f"做空入场上限{entry_high}高于下方清算集群上沿{below_high}，违反入场纪律"

        # 3. 止损距离检查（强制阻断）
        if atr_1h > 0:
            if direction == "long":
                worst_entry = entry_high
                stop_distance = worst_entry - stop
            else:
                worst_entry = entry_low
                stop_distance = stop - worst_entry
            min_distance = 1.2 * atr_1h
            if stop_distance < min_distance:
                return False, f"止损距离{stop_distance:.2f}小于1.2倍1h ATR({min_distance:.2f})"

        # 4. 深度思考痕迹检查（仅警告）
        if "自我质疑" not in reasoning:
            logger.warning("策略校验警告：reasoning中缺少'自我质疑'环节")
        if "如果我错了" not in reasoning:
            logger.warning("策略校验警告：reasoning中缺少'如果我错了'的反思陈述")

        # 5. 仓位与矛盾信号联动检查（强制降级）
        contradiction_keywords = ["矛盾", "背离", "冲突", "不一致"]
        has_contradiction = any(kw in reasoning for kw in contradiction_keywords)
        if has_contradiction and s.get("position_size") == "heavy":
            logger.warning("策略校验警告：存在矛盾信号但仓位为heavy，已强制降级为medium")
            s["position_size"] = "medium"
        elif has_contradiction and s.get("position_size") == "medium":
            logger.info("策略校验：存在矛盾信号，仓位保持medium")
        
        # 6. 期权逻辑检查（仅警告，不阻断）
        if data:
            current = data.get("mark_price", 0)
            max_pain = data.get("max_pain", 0)
            if current > max_pain and direction == "long":
                if "支持向上" in reasoning and "最大痛点" in reasoning:
                    logger.warning("策略校验警告：期权最大痛点低于现价，但reasoning中误判为支持向上")
            elif current < max_pain and direction == "short":
                if "支持向下" in reasoning and "最大痛点" in reasoning:
                    logger.warning("策略校验警告：期权最大痛点高于现价，但reasoning中误判为支持向下")

        return True, ""
    except Exception as e:
        return False, f"数值解析失败: {e}"
