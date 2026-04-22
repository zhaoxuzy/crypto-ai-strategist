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

第六步：矛盾裁决与方向判断
交叉验证与裁决：
如果我错了，最可能是因为：
方向选择（仅输出 long/short/neutral）及置信度（high/medium/low）：

【注意】你只需要输出方向和置信度，具体的入场、止损、止盈将由系统根据风控模型自动计算。若方向为 neutral，则无需提供价格。

输出JSON（不要代码块）：
{{
  "direction": "long/short/neutral",
  "confidence": "high/medium/low",
  "position_size": "light/medium/heavy/none",
  "reasoning": "（完整的六步推演内容）",
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


def calculate_entry_stop_tp(direction: str, data: dict) -> dict:
    """科学计算入场、止损、止盈"""
    current = data['mark_price']
    atr_1h = data.get('atr_1h', data['atr_15m'] * 2)
    above_cluster = data.get('above_cluster', '')
    below_cluster = data.get('below_cluster', '')
    
    # 解析清算集群
    above_low = above_high = below_low = below_high = None
    if '-' in above_cluster:
        above_low, above_high = map(float, above_cluster.split('-'))
    if '-' in below_cluster:
        below_low, below_high = map(float, below_cluster.split('-'))
    
    if direction == "long":
        # 入场：必须突破上方清算下沿，且不能远离现价
        if above_low is None:
            return None
        entry_low = above_low
        entry_high = min(current * 1.003, above_high)  # 现价+0.3%以内
        if entry_low > entry_high:
            entry_high = entry_low + atr_1h * 0.5
        
        # 止损：技术结构 + ATR缓冲
        technical_stop = below_low if below_low else entry_low - atr_1h * 1.5
        stop = min(entry_low - atr_1h * 1.5, technical_stop)
        
        # 止盈：基于2:1盈亏比，但不超过上方清算上沿
        risk = entry_high - stop
        tp_by_rr = entry_high + risk * 2.0
        tp = min(tp_by_rr, above_high) if above_high else tp_by_rr
        
    else:  # short
        if below_high is None:
            return None
        entry_high = below_high
        entry_low = max(current * 0.997, below_low)  # 现价-0.3%以内
        if entry_low > entry_high:
            entry_low = entry_high - atr_1h * 0.5
        
        technical_stop = above_high if above_high else entry_high + atr_1h * 1.5
        stop = max(entry_high + atr_1h * 1.5, technical_stop)
        
        risk = stop - entry_low
        tp_by_rr = entry_low - risk * 2.0
        tp = max(tp_by_rr, below_low) if below_low else tp_by_rr
    
    # 校验盈亏比
    if direction == "long":
        rr = (tp - entry_high) / (entry_high - stop)
    else:
        rr = (entry_low - tp) / (stop - entry_low)
    
    if rr < 1.8:  # 放宽至1.8，因为计算更保守
        logger.warning(f"计算出的盈亏比{rr:.2f}低于1.8，信号可能被过滤")
        # 不直接返回None，由上层决定
    
    return {
        "entry_price_low": round(entry_low, 2),
        "entry_price_high": round(entry_high, 2),
        "stop_loss": round(stop, 2),
        "take_profit": round(tp, 2),
        "calculated_rr": round(rr, 2)
    }


def validate_and_enrich_strategy(s: dict, data: dict) -> tuple[bool, str, dict]:
    """校验AI输出，并计算科学点位"""
    direction = s.get("direction")
    if direction not in ["long", "short", "neutral"]:
        return False, f"无效方向: {direction}", s
    
    if direction == "neutral":
        s["signal_type"] = "neutral"
        s["confidence"] = "low"
        s["entry_price_low"] = 0
        s["entry_price_high"] = 0
        s["stop_loss"] = 0
        s["take_profit"] = 0
        return True, "", s
    
    # 检查深度思考痕迹
    reasoning = s.get("reasoning", "")
    if "自我质疑" not in reasoning:
        logger.warning("缺少自我质疑环节")
    
    # 计算点位
    points = calculate_entry_stop_tp(direction, data)
    if points is None:
        return False, "无法计算入场点位（清算集群数据缺失）", s
    
    # 校验入场区是否紧贴现价
    current = data['mark_price']
    if direction == "long" and points["entry_price_low"] < current * 0.995:
        return False, f"做多入场下限{points['entry_price_low']:.2f}低于现价过多", s
    if direction == "short" and points["entry_price_high"] > current * 1.005:
        return False, f"做空入场上限{points['entry_price_high']:.2f}高于现价过多", s
    
    # 合并点位
    s.update(points)
    s["signal_type"] = "immediate"
    s["execution_plan"] = "立即入场"
    
    # 仓位联动矛盾信号
    contradiction_kws = ["矛盾", "背离", "冲突"]
    if any(kw in reasoning for kw in contradiction_kws):
        if s.get("position_size") == "heavy":
            s["position_size"] = "medium"
            logger.warning("存在矛盾信号，仓位降为medium")
    
    return True, "", s
