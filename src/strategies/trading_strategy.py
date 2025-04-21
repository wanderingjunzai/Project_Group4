import logging
from datetime import datetime
from vnpy.trader.utility import ArrayManager
from vnpy.trader.object import (
    TickData, 
    BarData,
    OrderData,
    TradeData
)
from vnpy.trader.constant import (
    Direction,
    Offset,
    Status,
    Exchange,
    Interval,
    OrderType
)
from vnpy_ctastrategy import (
    CtaTemplate,
    StopOrder
)
from typing import List, Dict, Set
import os

class MediumFrequencyStrategy(CtaTemplate):
    author = "策略作者"
    
    # 策略参数
    fast_window = 10        # 保持10
    slow_window = 20        # 保持20
    rsi_window = 14         # 保持14
    rsi_entry = 35          # 保持35
    min_volume = 0.01       # 从500改为0.01，适合BTC的最小交易量
    price_change_threshold = 0.01  # 保持0.01
    risk_percent = 0.02     # 每笔交易的资金比例
    
    # 策略变量
    fast_ma0 = 0.0
    slow_ma0 = 0.0
    rsi_value = 0.0
    pos_price = 0.0
    last_trade_price = 0.0  # 上次交易价格
    
    parameters = ["fast_window", "slow_window", "rsi_window", "rsi_entry", "min_volume", "price_change_threshold", "risk_percent"]
    variables = ["fast_ma0", "slow_ma0", "rsi_value", "pos_price", "last_trade_price"]
    
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        """策略初始化"""
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        
        # 设置日志
        self.logger = logging.getLogger(strategy_name)
        self.logger.setLevel(logging.INFO)
        
        # 移除所有现有的处理器
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
        
        # 创建文件处理器 - 使用绝对路径
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"{strategy_name}.log")
        
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        
        # 设置日志格式
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(message)s')
        file_handler.setFormatter(formatter)
        
        # 添加处理器
        self.logger.addHandler(file_handler)
        
        # 防止日志传播到根日志记录器
        self.logger.propagate = False
        
        # 参数设置
        self.fast_window = setting.get('fast_window', 10)
        self.slow_window = setting.get('slow_window', 20)
        self.rsi_window = setting.get('rsi_window', 14)
        self.rsi_entry = setting.get('rsi_entry', 35)
        self.min_volume = setting.get('min_volume', 0.01)  # 修改为0.01
        self.price_change_threshold = setting.get('price_change_threshold', 0.01)
        self.risk_percent = setting.get('risk_percent', 0.02)
        
        # 变量初始化
        self.fast_ma0 = 0.0
        self.slow_ma0 = 0.0
        self.rsi_value = 0.0
        self.last_trade_price = 0.0
        
        # 指标初始化
        self.am = ArrayManager(100)
        self.active_orders = set()
        
        self.trading_executor = None

        self.logger.info("策略初始化完成")
        self.write_log("策略初始化完成")
        self.write_log(f"日志文件路径: {log_file}")

    def write_log(self, msg: str):
        """重写日志方法"""
        # 只通过logger记录，不打印到控制台
        self.logger.info(msg)
        # 同时调用父类的write_log方法，确保日志也被VN Trader记录
        super().write_log(msg)

    def write_debug(self, msg: str):
        """写入调试信息"""
        # 只通过logger记录，不打印到控制台
        self.logger.debug(msg)
        # 父类没有write_debug方法，所以不调用super().write_debug
        # 如果需要，可以将调试信息也写入到日志文件中
        # 或者使用write_log方法记录重要的调试信息

    def on_init(self):
        """策略初始化回调"""
        self.write_log("策略初始化")
        self.load_bar(1)  # 加载日线数据

    def on_start(self):
        """策略启动回调"""
        self.write_log("策略启动")
        
    def on_stop(self):
        """策略停止回调"""
        self.write_log("策略停止")

    def on_tick(self, tick: TickData):
        """行情更新回调"""
        # 减少日志输出，只在调试模式下输出
        self.write_debug(f"收到行情更新 - {tick.symbol}")
        self.write_debug(f"最新价: {tick.last_price}, 买一价: {tick.bid_price_1}, 卖一价: {tick.ask_price_1}")

    def calculate_trade_volume(self, price):
        """计算交易量
        使用账户资金的固定比例来计算交易量
        """
        # 在回测和实盘环境中获取资金的方式不同
        try:
            # 尝试多种方式获取账户资金
            balance = 10000  # 默认资金，以防无法获取
            
            # 方法1: 尝试从cta_engine获取
            if hasattr(self, 'cta_engine'):
                if hasattr(self.cta_engine, 'capital'):
                    balance = self.cta_engine.capital
                elif hasattr(self.cta_engine, 'engine') and hasattr(self.cta_engine.engine, 'capital'):
                    balance = self.cta_engine.engine.capital
            
            # 方法2: 尝试从主引擎获取账户信息
            if balance == 10000 and hasattr(self, 'cta_engine') and hasattr(self.cta_engine, 'main_engine'):
                # 获取所有账户，查找USDT资产
                accounts = self.cta_engine.main_engine.get_all_accounts()
                for account in accounts:
                    if account.accountid.upper() == 'USDT':
                        balance = account.balance
                        break
            
            self.write_log(f"获取到账户资金: {balance}")
        except Exception as e:
            self.write_log(f"获取账户资金失败: {e}，使用默认资金值")
            balance = 10000  # 默认资金
        
        # 使用账户资金的risk_percent比例来计算交易金额
        trade_amount = balance * self.risk_percent
        # 计算交易量（向下取整到0.001）
        volume = round(trade_amount / price / 0.001) * 0.001
        # 确保交易量不小于最小交易量
        return max(volume, self.min_volume)

    def on_bar(self, bar: BarData):
        """K线更新回调"""
        self.am.update_bar(bar)
        if not self.am.inited:
            return

        # 计算指标
        fast_ma = self.am.sma(self.fast_window)
        slow_ma = self.am.sma(self.slow_window)
        rsi_value = self.am.rsi(self.rsi_window)
        
        # 记录K线信息
        self.write_debug(f"\n{'='*50}")
        self.write_debug(f"K线时间(回测): {bar.datetime}")
        self.write_debug(f"开盘价: {bar.open_price:.2f}")
        self.write_debug(f"最高价: {bar.high_price:.2f}")
        self.write_debug(f"最低价: {bar.low_price:.2f}")
        self.write_debug(f"收盘价: {bar.close_price:.2f}")
        self.write_debug(f"成交量: {bar.volume}")
        
        # 记录指标信息
        self.write_debug(f"\n技术指标:")
        self.write_debug(f"快速MA: {fast_ma:.2f}")
        self.write_debug(f"慢速MA: {slow_ma:.2f}")
        self.write_debug(f"RSI: {rsi_value:.2f}")
        
        # 交易信号判断
        if self.pos > 0:
            # 平仓条件：RSI过高或均线死叉，且价格变动超过阈值
            price_change = (bar.close_price - self.last_trade_price) / self.last_trade_price
            if (rsi_value > 65 or fast_ma < slow_ma) and abs(price_change) > self.price_change_threshold:
                self.write_log(f"\n>>> 平仓信号 <<<")
                self.write_log(f"信号时间(回测): {bar.datetime}")
                self.write_log(f"触发条件: RSI={rsi_value:.2f} > 65 或 快线{fast_ma:.2f} < 慢线{slow_ma:.2f}")
                self.write_log(f"价格变动: {price_change:.2%}")
                self.sell(bar.close_price, abs(self.pos))
                self.write_log(f"平仓价格: {bar.close_price:.2f}")
                self.last_trade_price = bar.close_price

        elif self.pos == 0:
            # 开仓条件：RSI较低且均线金叉，且成交量足够
            if (rsi_value < 35 and fast_ma > slow_ma and 
                bar.volume >= self.min_volume):
                # 计算交易量
                trade_volume = self.calculate_trade_volume(bar.close_price)
                self.write_log(f"\n>>> 开仓信号 <<<")
                self.write_log(f"信号时间(回测): {bar.datetime}")
                self.write_log(f"触发条件: RSI={rsi_value:.2f} < 35 且 快线{fast_ma:.2f} > 慢线{slow_ma:.2f}")
                self.write_log(f"成交量: {bar.volume}")
                self.write_log(f"计划交易量: {trade_volume}")
                self.buy(bar.close_price, trade_volume)
                self.write_log(f"开仓价格: {bar.close_price:.2f}")
                self.last_trade_price = bar.close_price

    def on_order(self, order: OrderData):
        """订单更新回调"""
        # 获取订单时间，如果没有则使用当前时间
        order_time = getattr(order, 'datetime', None)
        if not order_time:
            order_time = datetime.now()
            
        self.write_log(f"\n=== 订单更新 ===")
        self.write_log(f"订单时间(回测): {order_time}")
        self.write_log(f"订单编号: {order.orderid}")
        self.write_log(f"订单状态: {order.status}")
        self.write_log(f"委托价格: {order.price}")
        self.write_log(f"委托数量: {order.volume}")
        self.write_log(f"委托方向: {order.direction}")
        self.write_log(f"委托类型: {order.type}")
        self.write_log(f"交易所: {order.exchange}")

    def on_trade(self, trade: TradeData):
        """成交回调"""
        # 获取成交时间，如果没有则使用当前时间
        trade_time = getattr(trade, 'datetime', None)
        if not trade_time:
            trade_time = datetime.now()
            
        self.write_log(f"\n=== 成交信息 ===")
        self.write_log(f"成交时间(回测): {trade_time}")
        self.write_log(f"成交编号: {trade.tradeid}")
        self.write_log(f"成交方向: {trade.direction}")
        self.write_log(f"成交价格: {trade.price}")
        self.write_log(f"成交数量: {trade.volume}")
        self.write_log(f"成交金额: {trade.price * trade.volume:.2f}")
        self.write_log(f"交易所: {trade.exchange}")
        self.write_log(f"当前持仓: {self.pos}")
        
        # 计算当前盈亏
        if self.pos != 0:
            profit = (trade.price - self.last_trade_price) * self.pos
            profit_pct = (trade.price - self.last_trade_price) / self.last_trade_price * 100
            self.write_log(f"当前盈亏: {profit:.2f} ({profit_pct:.2f}%)")