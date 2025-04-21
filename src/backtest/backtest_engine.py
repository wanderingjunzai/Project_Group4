from datetime import datetime
import sys
import logging
from pathlib import Path
from typing import List, Dict
from vnpy.trader.object import BarData, OrderData, TradeData
from vnpy.trader.constant import Exchange, Interval, Status, Direction
from vnpy_ctastrategy.backtesting import BacktestingEngine, CtaTemplate, BacktestingMode
from pymongo import MongoClient
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import matplotlib.font_manager as fm
from matplotlib.widgets import Slider
import matplotlib.dates as mdates
from pandas import Timestamp
from scipy.signal import savgol_filter

class BacktestEngine:
    def __init__(self):
        """初始化回测引擎"""
        self.engine = BacktestingEngine()
        self.client = MongoClient('localhost', 27017)
        self.db = self.client.crypto_trading
        self.collection = self.db.market_data
        
        # 设置引擎基础参数
        self.init_capital = 1_000_000  # 初始资金100万
        self.contract_multiplier = 1    # 合约乘数
        self.commission_rate = 0.001    # 手续费率 0.1%
        self.price_tick = 0.01         # 价格精度

        self.setup_logging()

    def setup_logging(self):
        """配置日志系统"""
        # 创建logs目录
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        # 生成日志文件名，使用当前时间戳
        current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_filename = f"backtest_{current_time}.log"
        log_file = log_dir / log_filename
        
        # 配置根日志记录器之前先移除现有的处理器
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        
        # 移除现有的处理器，避免重复日志记录
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # 创建文件处理器
        try:
            file_handler = logging.FileHandler(
                filename=str(log_file),
                mode='a',
                encoding='utf-8'
            )
            
            # 设置日志格式
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(formatter)
            
            # 添加处理器
            logger.addHandler(file_handler)
            
            # 添加控制台处理器
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            
            print(f"日志文件保存在: {log_file}")
            
            # 写入一条测试日志
            logger.info("==== 回测日志系统初始化完成 ====")
            
        except Exception as e:
            print(f"配置日志系统时出错: {str(e)}")
            print(f"将仅使用控制台输出")
            
            # 确保至少有控制台输出
            console_handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

    def load_bar_data(self, symbol: str, start: datetime, end: datetime) -> List[BarData]:
        query = {
            "symbol": symbol,
            "datetime": {
                "$gte": start,
                "$lt": end
            }
        }
        
        cursor = self.collection.find(query).sort("datetime", 1)
        bars = []
        
        print(f"开始加载{symbol}的历史数据...")
        
        for doc in cursor:
            # 打印第一条数据用于调试
            if len(bars) == 0:
                print("首条数据样例:")
                print(doc)
                
            bar = BarData(
                symbol=doc["symbol"],
                exchange=Exchange.LOCAL,
                datetime=doc["datetime"],
                interval=Interval.MINUTE,
                volume=float(doc["volume"]),
                open_price=float(doc["open"]),
                high_price=float(doc["high"]),
                low_price=float(doc["low"]),
                close_price=float(doc["close"]),
                gateway_name="BACKTEST"
            )
            bars.append(bar)
        
        print(f"数据加载完成，共{len(bars)}条K线")
        
        # 打印首尾数据时间用于验证
        if bars:
            print(f"数据时间范围: {bars[0].datetime} 到 {bars[-1].datetime}")
        
        return bars

    def run_backtest(self, strategy_class, setting: Dict, symbol: str, start: datetime, end: datetime):
        """运行回测"""
        logging.info(f"开始回测 - 策略: {strategy_class.__name__}, 交易对: {symbol}")
        logging.info(f"回测时间范围: {start} 到 {end}")
        logging.info(f"策略参数: {setting}")
        
        print("\n正在初始化回测引擎...")
        
        # 设置初始资金
        initial_capital = 1_000_000
        logging.info(f"初始资金: {initial_capital}")
        
        # 设置回测参数
        self.engine.set_parameters(
            vt_symbol=f"{symbol}.LOCAL",
            interval=Interval.MINUTE,
            start=start,
            end=end,
            rate=0.001,
            slippage=0,
            size=1,
            pricetick=0.01,
            capital=initial_capital
        )
        logging.info("回测参数设置完成")
        
        # 添加策略
        self.strategy = self.engine.add_strategy(strategy_class, setting)
        
        # 启用交易
        self.engine.strategy.trading = True
        
        # 加载数据
        bars = self.load_bar_data(symbol, start, end)
        if not bars:
            logging.error("加载数据失败，未找到符合条件的K线数据")
            return None
            
        logging.info(f"成功加载 {len(bars)} 条K线数据")
        logging.info("\n开始回测运行...")
        print("\n开始回测运行...")
        
        # 运行K线回放
        bar_count = 0
        for bar in bars:
            self.engine.new_bar(bar)
            bar_count += 1
            if bar_count % 1000 == 0:
                logging.info(f"已处理 {bar_count}/{len(bars)} 条K线数据")
        
        # 完成回测
        self.engine.run_backtesting()
        logging.info("回测运行完成")
        
        # 获取所有交易记录
        trades = self.engine.get_all_trades()
        if not trades:
            logging.warning("回测过程中没有产生任何交易")
            print("没有产生任何交易")
            return None
            
        logging.info(f"回测产生了 {len(trades)} 笔交易")
        
        # 创建结果DataFrame
        df = pd.DataFrame()
        df['datetime'] = [bar.datetime for bar in bars]
        df['close_price'] = [bar.close_price for bar in bars]
        df.set_index('datetime', inplace=True)
        
        # 计算资金曲线
        df['balance'] = pd.Series([initial_capital] * len(df), dtype='float64')
        current_position = 0
        current_balance = float(initial_capital)
        
        for trade in trades:
            idx = df.index.get_loc(trade.datetime)
            trade_value = float(trade.price * trade.volume)
            commission = float(trade_value * self.engine.rate)
            
            if trade.direction == Direction.LONG:
                current_position += trade.volume
                current_balance -= (trade_value + commission)
            else:
                current_position -= trade.volume
                current_balance += (trade_value - commission)
            
            df.loc[df.index[idx:], 'balance'] = pd.Series([current_balance] * (len(df) - idx), index=df.index[idx:])
        
        # 计算并打印关键指标
        stats = self.calculate_statistics(df)
        if stats:
            logging.info("\n=== 策略评估指标 ===")
            logging.info(f"总收益率: {stats['total_return']:.2f}%")
            logging.info(f"年化收益率: {stats['annual_return']:.2f}%")
            logging.info(f"夏普比率: {stats['sharpe_ratio']:.2f}")
            logging.info(f"信息比率: {stats['information_ratio']:.2f}")
            logging.info(f"最大回撤: {stats['max_drawdown']:.2f}%")
            logging.info(f"胜率: {stats['win_rate']:.2f}%")
            
            print("\n=== 策略评估指标 ===")
            print(f"夏普比率: {stats['sharpe_ratio']:.2f}")
            print(f"信息比率: {stats['information_ratio']:.2f}")
            print(f"最大回撤: {stats['max_drawdown']:.2f}%")
        
        return df

    def calculate_statistics(self, df) -> Dict[str, float]:
        """计算回测统计指标"""
        try:
            # 获取所有交易
            trades = self.engine.get_all_trades()
            if not trades or len(trades) == 0:
                return None
                
            # 基础数据准备
            initial_capital = self.engine.capital
            final_capital = df['balance'].iloc[-1]
            
            # 计算日收益率序列
            daily_returns = df['balance'].pct_change().fillna(0)
            
            # 计算总收益率和年化收益率
            total_return = (final_capital - initial_capital) / initial_capital
            days = (trades[-1].datetime - trades[0].datetime).days
            annual_return = ((1 + total_return) ** (365.0 / max(days, 1))) - 1 if days > 0 else 0
            
            # 计算夏普比率
            risk_free_rate = 0.02  # 年化无风险利率
            daily_risk_free = risk_free_rate / 252
            excess_returns = daily_returns - daily_risk_free
            returns_std = float(excess_returns.std() or 1e-6)  # 避免除以0
            sharpe_ratio = np.sqrt(252) * float(excess_returns.mean()) / returns_std
            
            # 计算信息比率
            benchmark_returns = pd.Series(0, index=df.index)  # 基准收益率设为0
            active_returns = daily_returns - benchmark_returns
            active_std = float(active_returns.std() or 1e-6)  # 避免除以0
            information_ratio = np.sqrt(252) * float(active_returns.mean()) / active_std
            
            # 计算最大回撤
            cummax = df['balance'].expanding().max()
            drawdown = (df['balance'] - cummax) / cummax
            max_drawdown = float(drawdown.min() or 0)
            
            # 计算胜率
            winning_trades = 0
            open_price = None
            for trade in trades:
                if trade.direction == Direction.LONG:  # 开仓
                    open_price = trade.price
                else:  # 平仓
                    if open_price is not None:
                        if trade.price > open_price:
                            winning_trades += 1
                        open_price = None
            total_complete_trades = len(trades) // 2
            win_rate = winning_trades / total_complete_trades if total_complete_trades > 0 else 0

            return {
                "total_return": total_return * 100,
                "annual_return": annual_return * 100,
                "sharpe_ratio": sharpe_ratio,
                "information_ratio": information_ratio,
                "max_drawdown": max_drawdown * 100,
                "win_rate": win_rate * 100,
                "total_trades": total_complete_trades,
                "drawdown_series": drawdown,
                "daily_returns": daily_returns
            }
            
        except Exception as e:
            print(f"计算统计指标时出错: {str(e)}")
            import traceback
            print(traceback.format_exc())
            return None

    def calculate_result(self, df, trades):
        """计算回测结果"""
        # 确保DataFrame中包含必要的数据
        df['close'] = self.engine.history_data['close_price']  # 添加收盘价数据
        df['datetime'] = self.engine.history_data.index  # 添加时间索引
        df['balance'] = df['balance'].fillna(method='ffill')  # 填充余额数据
        
        return df

    def get_strategy(self):
        """获取当前正在运行的策略实例"""
        return getattr(self, 'strategy', None)

    def plot_backtest_results(self, df, trades):
        """绘制回测结果图表"""
        try:
            # 计算统计指标
            stats = self.calculate_statistics(df)
            if stats is None:
                print("没有足够的交易数据来生成统计图表")
                return
                
            # 设置中文字体
            plt.rcParams['font.sans-serif'] = ['SimHei']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 设置图表样式
            plt.style.use('bmh')
            
            # 创建图表
            fig = plt.figure(figsize=(12, 8))
            
            # 创建网格布局 - 只有两个子图
            gs = plt.GridSpec(2, 1, height_ratios=[2, 1])
            
            # 1. 回撤分析图
            ax1 = plt.subplot(gs[0])
            ax1.set_title('最大回撤分析', pad=10)
            dates = pd.to_datetime(df.index)
            
            # 结合填充区域和线图显示回撤
            drawdown_series = stats['drawdown_series'] * 100
            ax1.fill_between(dates, drawdown_series, 0, 
                           color='#ffcdd2', alpha=0.3, label='回撤区间')
            ax1.plot(dates, drawdown_series, 
                    color='#d32f2f', linewidth=1, label='回撤线')
            
            # 标注最大回撤点
            max_drawdown_idx = drawdown_series.idxmin()
            max_drawdown_value = drawdown_series.min()
            ax1.scatter(max_drawdown_idx, max_drawdown_value, 
                       color='#b71c1c', s=100, zorder=5, label='最大回撤点')
            
            # 添加最大回撤标注
            ax1.annotate(f'最大回撤: {max_drawdown_value:.2f}%',
                        xy=(max_drawdown_idx, max_drawdown_value),
                        xytext=(10, -10), textcoords='offset points',
                        bbox=dict(boxstyle='round,pad=0.5', fc='#fff3e0', alpha=0.8),
                        arrowprops=dict(arrowstyle='->', color='#d32f2f'))
            
            # 添加0线
            ax1.axhline(y=0, color='black', linestyle='--', alpha=0.2)
            
            ax1.set_xlabel('日期')
            ax1.set_ylabel('回撤 (%)')
            ax1.grid(True, alpha=0.2)
            ax1.legend(loc='upper right', framealpha=0.9)
            
            # 优化y轴刻度
            ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: '{:.1f}%'.format(y)))
            
            # 2. 策略评估指标表格
            ax2 = plt.subplot(gs[1])
            ax2.set_title('策略评估指标', pad=10)
            ax2.axis('off')
            
            # 创建指标表格
            metrics = [
                ['总收益率', f"{stats['total_return']:.2f}%"],
                ['年化收益率', f"{stats['annual_return']:.2f}%"],
                ['夏普比率', f"{stats['sharpe_ratio']:.2f}"],
                ['信息比率', f"{stats['information_ratio']:.2f}"],
                ['最大回撤', f"{stats['max_drawdown']:.2f}%"],
                ['胜率', f"{stats['win_rate']:.2f}%"],
                ['总交易次数', f"{stats['total_trades']}"]
            ]
            
            # 创建更大的表格
            table = ax2.table(cellText=metrics,
                            loc='center',
                            cellLoc='left',
                            colWidths=[0.3, 0.3])
            table.auto_set_font_size(False)
            table.set_fontsize(10)
            table.scale(1.5, 2)
            
            # 调整布局
            plt.tight_layout()
            
            # 保存图片
            current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
            save_path = f'backtest_results_{current_time}.png'
            plt.savefig(save_path, dpi=300, bbox_inches='tight', facecolor='white')
            print(f"\n回测结果图表已保存至: {save_path}")
            
            # 显示图表
            plt.show()
            
        except Exception as e:
            print(f"绘图错误: {str(e)}")
            import traceback
            print(traceback.format_exc())