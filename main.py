import sys
print("=== Python 启动成功 ===", flush=True)

try:
    from utils.logger import logger
    print("✅ logger 导入成功", flush=True)
except Exception as e:
    print(f"❌ logger 导入失败: {e}", flush=True)
    sys.exit(1)

try:
    from data_fetcher.okx_rest import get_current_price
    print("✅ okx_rest 导入成功", flush=True)
except Exception as e:
    print(f"❌ okx_rest 导入失败: {e}", flush=True)
    sys.exit(1)

try:
    from data_fetcher.coinglass import CoinGlassClient
    print("✅ coinglass 导入成功", flush=True)
except Exception as e:
    print(f"❌ coinglass 导入失败: {e}", flush=True)
    sys.exit(1)

# 测试获取价格
try:
    price = get_current_price("BTC-USDT-SWAP")
    print(f"✅ OKX 价格获取成功: {price}", flush=True)
except Exception as e:
    print(f"❌ OKX 价格获取失败: {e}", flush=True)

# 测试 CoinGlass 基础请求
try:
    cg = CoinGlassClient()
    # 只请求一个最基础的接口
    funding = cg.get_funding_rate_history("BTC")
    print(f"✅ CoinGlass 资金费率获取成功: {funding}", flush=True)
except Exception as e:
    print(f"❌ CoinGlass 请求失败: {e}", flush=True)

print("=== 诊断完成 ===", flush=True)
