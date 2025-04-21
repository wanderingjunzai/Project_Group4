from vnpy.trader.constant import Exchange

def check_exchanges():
    print("VnPy支持的交易所列表：")
    for exchange in Exchange:
        print(f"- {exchange.value}")

if __name__ == "__main__":
    check_exchanges()