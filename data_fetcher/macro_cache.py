import os
import json
import time
import requests
from datetime import datetime
from utils.logger import logger

CACHE_FILE = "/tmp/macro_cache.json"

def _is_cache_valid():
    if not os.path.exists(CACHE_FILE):
        return False
    try:
        mtime = os.path.getmtime(CACHE_FILE)
        return (time.time() - mtime) < 24 * 3600
    except:
        return False

def _fetch_fear_greed():
    url = "https://api.alternative.me/fng/?limit=2"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("metadata", {}).get("error"):
            raise Exception(data["metadata"]["error"])
        current = data["data"][0]
        previous = data["data"][1] if len(data["data"]) > 1 else None
        fg_change = "N/A"
        if previous:
            diff = int(current["value"]) - int(previous["value"])
            fg_change = f"{diff:+d}"
        return {
            "value": current["value"],
            "classification": current["value_classification"],
            "change": fg_change
        }
    except Exception as e:
        logger.error(f"恐惧贪婪指数获取失败: {e}")
        return {"value": "50", "classification": "Neutral", "change": "0"}

def update_macro_cache():
    logger.info("更新宏观数据缓存...")
    cache = {
        "updated_at": datetime.now().isoformat(),
        "fear_greed": _fetch_fear_greed(),
        "exchange_balance_trend": "N/A",
        "active_addresses": "N/A"
    }
    glassnode_key = os.getenv("GLASSNODE_API_KEY", "")
    if glassnode_key and glassnode_key.lower() != "none":
        # Glassnode 接口如需启用可自行扩展，此处省略以保持简洁
        pass
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    logger.info(f"缓存已更新，恐惧贪婪指数: {cache['fear_greed']['value']}")

def get_macro_data():
    if not _is_cache_valid():
        update_macro_cache()
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except:
        return {
            "fear_greed": {"value": "50", "classification": "Neutral", "change": "0"},
            "exchange_balance_trend": "N/A",
            "active_addresses": "N/A"
        }
