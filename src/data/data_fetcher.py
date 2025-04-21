from typing import Optional
import pandas as pd
import ccxt
from datetime import datetime
from pymongo import MongoClient

class DataFetcher:
    def __init__(self):
        # 配置币安交易所访问
        self.exchange = ccxt.binance({
            'timeout': 30000,  # 增加超时时间到30秒
            'enableRateLimit': True,  # 启用请求频率限制
            'proxies': {
                'http': 'http://127.0.0.1:7890',  # 如果使用代理，请修改端口
                'https': 'http://127.0.0.1:7890'  # 如果使用代理，请修改端口
            }
        })
        
        # MongoDB连接保持不变
        self.client = MongoClient('localhost', 27017)
        self.db = self.client.crypto_trading
        self.collection = self.db.market_data
        print("MongoDB数据库初始化成功")
        
    def fetch_history(self, symbol: str, interval: str, 
                     start_time: str, end_time: Optional[str] = None) -> pd.DataFrame:
        """获取历史K线数据"""
        try:
            # 测试连接
            print("测试币安API连接...")
            self.exchange.load_markets()
            print("币安API连接成功")
            
            # 修正符号格式，去掉斜杠
            symbol = symbol.replace("/", "")  # 将 "BTC/USDT" 转换为 "BTCUSDT"
            
            # 修正时间间隔格式
            timeframes = {
                "1m": "1m",
                "5m": "5m",
                "15m": "15m",
                "1h": "1h",
                "4h": "4h",
                "1d": "1d"
            }
            
            start_timestamp = int(pd.Timestamp(start_time).timestamp() * 1000)
            end_timestamp = int(pd.Timestamp(end_time).timestamp() * 1000) if end_time else None
            
            print(f"开始获取{symbol}的历史数据...")
            print(f"起始时间: {start_time}")
            print(f"结束时间: {end_time}")
            print(f"时间间隔: {interval}")
            
            # 计算预期数据量
            start_dt = pd.Timestamp(start_time)
            end_dt = pd.Timestamp(end_time)
            days = (end_dt - start_dt).total_seconds() / (24 * 3600)
            expected_count = int(days * 1440)  # 每天1440条
            
            print(f"开始时间: {start_dt}")
            print(f"结束时间: {end_dt}")
            print(f"时间跨度: {days:.2f}天")
            print(f"预期数据量: {expected_count}条")
            
            all_data = []
            count = 0
            
            try:
                while True:
                    # 获取数据
                    ohlcv = self.exchange.fetch_ohlcv(
                        symbol,
                        timeframes[interval],
                        start_timestamp,
                        limit=1000
                    )
                    
                    if not ohlcv:
                        break
                        
                    all_data.extend(ohlcv)
                    count += len(ohlcv)
                    
                    if count % 10000 == 0:
                        print(f"已获取 {count} 条数据...")
                    
                    start_timestamp = ohlcv[-1][0] + 1
                    
                    if end_timestamp and start_timestamp >= end_timestamp:
                        break
                    
                    self.exchange.sleep(50)  # 避免请求过于频繁
                    
            except Exception as e:
                print(f"获取数据时发生错误: {str(e)}")
                raise e  # 抛出异常以便调试
            
            if not all_data:
                raise Exception("未获取到任何数据")
                
            print(f"共获取 {len(all_data)} 条数据")
            
            # 转换为DataFrame
            df = pd.DataFrame(all_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            
            # 严格过滤时间范围
            df = df[
                (df['datetime'] >= pd.Timestamp(start_time)) & 
                (df['datetime'] < pd.Timestamp(end_time))
            ]
            
            print(f"\n过滤后的数据统计:")
            print(f"开始时间: {df['datetime'].min()}")
            print(f"结束时间: {df['datetime'].max()}")
            print(f"数据条数: {len(df)}")
            
            # 数据完整性检查
            if len(all_data) != expected_count:
                print(f"警告：数据量不符合预期")
                print(f"预期：{expected_count}条")
                print(f"实际：{len(all_data)}条")
                
                # 检查缺失的时间段
                time_diff = df['datetime'].diff()
                gaps = time_diff[time_diff > pd.Timedelta(minutes=1)]
                if not gaps.empty:
                    print("\n数据缺失区间:")
                    for idx in gaps.index:
                        print(f"从 {df['datetime'][idx-1]} 到 {df['datetime'][idx]}")
            
            return df
        
        except ccxt.RequestTimeout as e:
            print(f"请求超时: {str(e)}")
            print("可能需要配置代理或检查网络连接")
            raise
        except Exception as e:
            print(f"获取数据时发生错误: {str(e)}")
            raise
    
    def save_to_database(self, df: pd.DataFrame, symbol: str, interval: str):
        """保存数据到MongoDB"""
        print(f"开始保存{symbol}的数据到数据库...")
        
        # 将DataFrame转换为字典列表
        records = []
        for _, row in df.iterrows():
            record = {
                "symbol": symbol,
                "datetime": row['datetime'],
                "interval": interval,
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row['volume'])
            }
            records.append(record)
            
            # 每1000条数据保存一次
            if len(records) >= 1000:
                self.collection.insert_many(records)
                print(f"已保存 {len(records)} 条数据...")
                records = []
        
        # 保存剩余数据
        if records:
            self.collection.insert_many(records)
            print(f"已保存剩余 {len(records)} 条数据")