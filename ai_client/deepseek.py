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
        above_high = float(parts[1])
        above_distance = f"+{above_high - current:.0f}"
    if below_cluster != 'N/A' and '-' in below_cluster:
        parts = below_cluster.split('-')
        below_low = float(parts[0])
        below_distance = f"-{current - below_low:.0f}"

    data_quality = data.get("data_quality", {})
    missing = [k for k, v in data_quality.items() if v == "❌ 缺失"]
    missing_str = "、".join(missing) if missing else "无"

    max_pain = data['max_pain']
    max_pain_bias = "偏空信号" if current > max_pain else "偏多信号"
    put_call_ratio = data['put_call_ratio']
    pc_bias = "偏空信号" if put_call_ratio > 1.0 else "偏多信号"

    prompt = f"""你是一个管理200万U的顶尖加密货币短线交易员。请严格按六步推演，每步包含“分析数据”、“第一反应”、“自我质疑”、“最终结论”。

【{symbol} | {timestamp}】
价格：{current:.2f} | 15min ATR：{data['atr_15m']:.2f} | 1h ATR：{data.get('atr_1h', data['atr_15m']*2):.2f} | 波动因子：{data['vol_factor']:.2f} | 7日分位数：{data['price_percentile']:.0f}%

清算池：
上方(空头)：{data['above_liq']/1e9:.2f}B，{above_cluster} (距{above_distance})
下方(多头)：{data['below_liq']/1e9:.2f}B，{below_cluster} (距{below_distance})
比值：{data['liq_ratio']:.3f}

订单簿：买{data['orderbook_bids']/1e6:.1f}M / 卖{data['orderbook_asks']/1e6:.1f}M | 失衡率{data['orderbook_imbalance']:.4f}
资金费率：{data['funding_rate']:.4f}% (分位{data['funding_percentile']:.0f}%)
OI：{data['oi']/1e9:.2f}B (分位{data['oi_percentile']:.0f}%)，24h{data['oi_change_24h']:+.1f}%
顶级多空比：{data['top_ls_ratio']:.2f} (分位{data['top_ls_percentile']:.0f}%)
恐慌贪婪：{data['fear_greed']} | CVD斜率：{data['cvd_slope']:.4f}
期权：最大痛点{max_pain:.2f} ({max_pain_bias}) | P/C比{put_call_ratio:.4f} ({pc_bias})
ETH/BTC：{data['eth_btc_ratio']:.4f} | 数据缺失：{missing_str}

---
第一步：环境定调（价格分位数、ATR、波动因子）
第一反应：
自我质疑：
最终结论：

第二步：猎物定位（清算池距离/强度、订单簿）
第一反应：
自我质疑：
最终结论：

第三步：对手盘解剖（OI、资金费率、顶级多空比）
第一反应：
自我质疑：
最终结论：

第四步：资金流验证（CVD斜率、期货净流、交易所余额）
第一反应：
自我质疑：
最终结论：

第五步：辅助信号（期权、ETH/BTC）
第一反应：
自我质疑：
最终结论：

第六步：矛盾裁决与决策
交叉验证：
最终裁决：
如果我错了，最可能是因为：
方向选择（long/short/neutral）：
置信度（high/medium/low）：
仓位（light/medium/heavy）：

【价格路径推演 - 三段式】
请用“先……然后……最后……”详细描述价格最可能的演变过程，每段必须包含：
- 具体价格目标
- 触发该段运动的盘口信号或条件
- 预计持续时间

入场区间（并说明为何选择此区间）：
止损位（并说明依据的技术结构或清算墙）：
止盈位（并说明与流动性池或结构的关系）：
主动证伪信号（时间/价格/指标条件）：
微观盘口确认（什么细节会让你延迟3秒入场）：

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
  "reasoning": "（完整的六步推演内容，包含上述所有环节）",
  "risk_note": "主要风险和证伪条件"
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
        raise ValueError("未找到 JSON")
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
    raise ValueError("未找到匹配花括号")


def call_deepseek(prompt: str, max_retries: int = 3) -> dict:
    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1",
        timeout=180.0
    )
    for attempt in range(max_retries):
        try:
            logger.info(f"DeepSeek 调用 (尝试 {attempt+1}/{max_retries})")
            resp = client.chat.completions.create(
                model="deepseek-reasoner",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=6000,
                timeout=180
            )
            content = resp.choices[0].message.content or ""
            reasoning = getattr(resp.choices[0].message, 'reasoning_content', None)
            _log_response_to_file(prompt, content, reasoning)
            if not content.strip():
                raise ValueError("空响应")
            json_str = extract_json_from_content(content)
            s = json.loads(json_str)
            s.setdefault("position_size", "none")
            s.setdefault("execution_plan", "")
            s.setdefault("reasoning", "")
            s.setdefault("risk_note", "")
            return s
        except Exception as e:
            logger.warning(f"调用失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                raise
    return {}


def validate_strategy(s: dict, data: dict = None) -> tuple[bool, str]:
    """
    校验 AI 输出的策略。
    - 若 data 未提供，仅做基础字段校验。
    - 若 data 提供，则计算实际盈亏比和止损距离，若低于安全阈值则发出警告，但不阻断信号。
    返回 (bool, str)，True 表示信号可推送，False 表示存在严重格式错误。
    """
    direction = s.get("direction")
    if direction not in ["long", "short", "neutral"]:
        return False, f"无效方向: {direction}"

    if direction == "neutral":
        s["signal_type"] = "neutral"
        s["confidence"] = "low"
        s["entry_price_low"] = 0
        s["entry_price_high"] = 0
        s["stop_loss"] = 0
        s["take_profit"] = 0
        return True, ""

    # 基础字段存在性检查
    required = ["entry_price_low", "entry_price_high", "stop_loss", "take_profit"]
    for field in required:
        if s.get(field) is None:
            return False, f"缺少必要字段: {field}"

    try:
        entry_low = float(s["entry_price_low"])
        entry_high = float(s["entry_price_high"])
        stop = float(s["stop_loss"])
        tp = float(s["take_profit"])
    except:
        return False, "价格字段必须为数字"

    if entry_low <= 0 or entry_high <= 0 or stop <= 0 or tp <= 0:
        return False, "价格必须为正数"
    if entry_low > entry_high:
        return False, "入场区间下限大于上限"

    # 如果有 data，进行风控校验（仅警告，不阻断）
    if data:
        atr_1h = data.get('atr_1h', data.get('atr_15m', 0) * 2)
        current = data.get('mark_price', 0)

        # 1. 盈亏比计算
        if direction == "long":
            worst_entry = entry_high
            risk = worst_entry - stop
            reward = tp - worst_entry
        else:
            worst_entry = entry_low
            risk = stop - worst_entry
            reward = worst_entry - tp

        if risk > 0:
            rr = reward / risk
            s["_calculated_rr"] = round(rr, 2)
            if rr < 1.5:
                logger.warning(f"盈亏比 {rr:.2f} 低于 1.5，建议人工复核")
        else:
            logger.warning("止损距离为零或负，风险计算异常")

        # 2. 止损距离校验
        if atr_1h > 0:
            stop_distance = abs(worst_entry - stop)
            min_distance = 1.2 * atr_1h
            if stop_distance < min_distance:
                logger.warning(f"止损距离 {stop_distance:.2f} 小于 1.2倍1h ATR ({min_distance:.2f})")

        # 3. 入场区与现价关系检查
        if current > 0:
            if direction == "long" and entry_low < current * 0.995:
                logger.warning(f"做多入场下限 {entry_low:.2f} 低于现价 {current:.2f} 较多，可能为挂单策略")
            if direction == "short" and entry_high > current * 1.005:
                logger.warning(f"做空入场上限 {entry_high:.2f} 高于现价 {current:.2f} 较多，可能为挂单策略")

    # 深度思考痕迹检查
    reasoning = s.get("reasoning", "")
    if "自我质疑" not in reasoning:
        logger.warning("reasoning 中缺少'自我质疑'环节")

    s["signal_type"] = "immediate"
    return True, ""
