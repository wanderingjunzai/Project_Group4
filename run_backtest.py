import logging
from datetime import datetime
from src.backtest.backtest_engine import BacktestEngine
from src.strategies.trading_strategy import MediumFrequencyStrategy

def main():
    try:
        # 配置基础日志
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(filename="logs/backtest.log", mode="a", encoding="utf-8")
            ]
        )
        
        print("开始回测程序...")
        
        engine = BacktestEngine()
        
        # 回测区间
        start = datetime(2024, 1, 1)
        end = datetime(2024, 12, 31)
        print(f"\n回测时间: {start} 到 {end}")
        
        # 策略参数
        setting = {
            "fast_window": 8,
            "slow_window": 15,
            "rsi_window": 10,
            "rsi_entry": 38,
            "min_volume": 0.005,
            "price_change_threshold": 0.008
        }
        
        # 运行回测前检查策略类
        if not hasattr(MediumFrequencyStrategy, 'buy'):
            print("错误: 策略类未正确继承 CtaTemplate!")
            return
            
        # 运行回测
        result = engine.run_backtest(
            strategy_class=MediumFrequencyStrategy,
            setting=setting,
            symbol="BTCUSDT",
            start=start,
            end=end
        )
        
        # 输出回测结果
        if result is not None:
            print("\n====== 回测结果 ======")
            # 获取回测统计指标
            statistics = engine.calculate_statistics(result)
            if statistics:
                print(f"总收益率: {statistics['total_return']:.2f}%")
                print(f"年化收益率: {statistics['annual_return']:.2f}%")
                print(f"夏普比率: {statistics['sharpe_ratio']:.2f}")
                print(f"最大回撤: {statistics['max_drawdown']:.2f}%")
                print(f"胜率: {statistics['win_rate']:.2f}%")
                
                # 添加详细的交易统计
                print("\n====== 交易明细 ======")
                trades = engine.engine.get_all_trades()
                if trades:
                    print(f"首笔交易时间: {trades[0].datetime}")
                    print(f"末笔交易时间: {trades[-1].datetime}")
                    print(f"交易天数: {(trades[-1].datetime - trades[0].datetime).days}天")
                    
                    # 绘制回测结果图表
                    engine.plot_backtest_results(result, trades)
            else:
                print("未能计算统计指标")
        else:
            print("\n回测未生成有效结果")
            
    except Exception as e:
        print(f"回测错误: {str(e)}")
        import traceback
        print(traceback.format_exc())

if __name__ == "__main__":
    main()