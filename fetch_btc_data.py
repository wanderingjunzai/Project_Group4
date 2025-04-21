from datetime import datetime
from src.data.data_fetcher import DataFetcher
import pandas as pd

def main():
    try:
        print("开始获取BTC/USDT数据...")
        
        # 初始化数据获取器
        fetcher = DataFetcher()
        
        # 设置时间范围
        start_time = "2024-01-01 00:00:00"
        end_time = "2025-01-31 00:00:00"
        
        # 计算预期数据量
        days = (pd.Timestamp(end_time) - pd.Timestamp(start_time)).days
        expected_records = days * 1440
        print(f"预期总数据量: {expected_records}条 (约{days}天)")
        
        # 分批获取数据(每次30天)
        current_start = pd.Timestamp(start_time)
        batch_days = 30
        
        while current_start < pd.Timestamp(end_time):
            # 计算当前批次的结束时间
            current_end = min(
                current_start + pd.Timedelta(days=batch_days),
                pd.Timestamp(end_time)
            )
            
            print(f"\n获取时间段: {current_start} 到 {current_end}")
            
            # 获取数据
            data = fetcher.fetch_history(
                symbol="BTCUSDT",
                interval="1m",
                start_time=str(current_start),
                end_time=str(current_end)
            )
            
            # 保存到数据库
            fetcher.save_to_database(data, "BTCUSDT", "1m")
            
            # 更新起始时间
            current_start = current_end
            
            print(f"完成当前批次数据获取和保存")
        
        print("\n所有数据获取完成！")
        
    except Exception as e:
        print(f"发生错误: {str(e)}")
        import traceback
        print(traceback.format_exc())

if __name__ == "__main__":
    main()