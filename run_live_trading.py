import sys
import time
import logging
import threading
import os
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy_binance import BinanceSpotGateway
from vnpy.trader.object import SubscribeRequest, OrderRequest, Direction, Offset, OrderType, LogData, BarData, CancelRequest, TickData, OrderData, TradeData
from vnpy.trader.constant import Exchange, Interval, Status
from vnpy.event import Event
from vnpy.trader.event import EVENT_CONTRACT, EVENT_ACCOUNT, EVENT_LOG, EVENT_POSITION, EVENT_TRADE, EVENT_ORDER, EVENT_TICK
from vnpy.trader.setting import SETTINGS
from vnpy.trader.database import get_database
import json
from pathlib import Path
from vnpy_ctastrategy.backtesting import BacktestingEngine
from src.strategies.trading_strategy import MediumFrequencyStrategy
import pandas as pd
from datetime import datetime, timedelta
import random
import numpy as np  # 添加numpy用于统计计算
import math
from typing import Optional, List, Dict, Any, Callable

# 禁止Qt输出警告和错误信息
os.environ["QT_LOGGING_RULES"] = "*=false"

# 创建logs目录
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(logs_dir, exist_ok=True)

# 配置日志
logging.basicConfig(level=logging.INFO)
SETTINGS["log.active"] = True
SETTINGS["log.level"] = logging.INFO
SETTINGS["log.console"] = True

# 添加文件日志处理器，使用固定文件名，追加模式
log_file = os.path.join(logs_dir, "trading_log.log")
file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='a')  # 使用追加模式
file_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logging.getLogger().addHandler(file_handler)
print(f"日志文件路径: {log_file} (追加模式)")

# 配置数据库（必须在创建MainEngine之前）
try:
    # 检查是否安装了pymongo
    import pymongo
    
    # 配置MongoDB
    SETTINGS.update({
        "database.active": True,
        "database.driver": "mongodb",
        "database.database": "vnpy",
        "database.host": "localhost",
        "database.port": 27017,
        "database.username": "",
        "database.password": ""
    })
    
    # 测试数据库连接
    database = get_database()
    if not database:
        print("Warning: Failed to connect to MongoDB, falling back to SQLite")
        SETTINGS["database.driver"] = "sqlite"  # 如果MongoDB连接失败，回退到SQLite
    else:
        print("Successfully connected to MongoDB")
        
except ImportError:
    print("Warning: pymongo not installed, using SQLite instead")
    SETTINGS["database.driver"] = "sqlite"

# 创建一个全局的持仓跟踪器
global_position_tracker = None

# 添加一个全局变量控制策略线程
strategy_running = True

# 添加一个简单的手动TCA分析器
class SimpleTCA:
    """简单的交易成本分析器，手动记录订单和成交数据"""
    
    def __init__(self, analysis_interval=5):
        """初始化分析器"""
        self.order_records = []  # 订单记录 [时间, 价格, 方向, 数量, 订单ID]
        self.trade_records = []  # 成交记录 [时间, 价格, 方向, 数量, 订单ID]
        self.analysis_interval = analysis_interval
        self.trade_count = 0
    
    def record_order(self, price, direction, volume, order_id):
        """记录订单"""
        self.order_records.append({
            "time": datetime.now(),
            "price": price,
            "direction": direction,
            "volume": volume,
            "order_id": order_id
        })
        print(f"TCA: 记录订单 {order_id}, 价格: {price}, 方向: {direction}")
    
    def record_trade(self, price, direction, volume, order_id):
        """记录成交"""
        self.trade_records.append({
            "time": datetime.now(),
            "price": price,
            "direction": direction,
            "volume": volume,
            "order_id": order_id
        })
        self.trade_count += 1
        print(f"TCA: 记录成交 {order_id}, 价格: {price}, 方向: {direction}")
        
        # 每N笔交易分析一次
        if self.trade_count % self.analysis_interval == 0:
            self.analyze()
    
    def analyze(self):
        """分析交易成本"""
        if not self.trade_records:
            print("没有足够的交易数据进行分析")
            return
            
        print("\n=== 交易成本分析 ===")
        analysis_results = ["\n=== 手动交易成本分析(TCA) ==="]
        
        # 计算滑点
        slippage_list = []  # 实际滑点
        slippage_percent_list = []  # 百分比滑点
        trade_delay_list = []  # 交易延迟(ms)
        
        for trade in self.trade_records:
            # 查找对应的订单
            matching_orders = [o for o in self.order_records if o["order_id"] == trade["order_id"]]
            if not matching_orders:
                continue
                
            order = matching_orders[0]
            
            # 计算滑点 (成交价格 - 订单价格)
            if trade["direction"] == Direction.LONG or trade["direction"] == "多":
                # 买入，成交价减去订单价，正值表示不利滑点
                slippage = trade["price"] - order["price"]
            else:
                # 卖出，订单价减去成交价，正值表示不利滑点
                slippage = order["price"] - trade["price"]
                
            slippage_list.append(slippage)
            
            # 计算百分比滑点
            if order["price"] > 0:
                slippage_percent = (slippage / order["price"]) * 100
                slippage_percent_list.append(slippage_percent)
            
            # 计算交易延迟
            if "time" in trade and "time" in order:
                delay_ms = (trade["time"] - order["time"]).total_seconds() * 1000
                trade_delay_list.append(delay_ms)
        
        # 滑点分析
        if slippage_list:
            avg_slippage = sum(slippage_list) / len(slippage_list)
            max_slippage = max(slippage_list)
            min_slippage = min(slippage_list)
            
            # 标准差计算
            std_slippage = math.sqrt(sum((x - avg_slippage) ** 2 for x in slippage_list) / len(slippage_list)) if len(slippage_list) > 1 else 0
            
            print(f"滑点分析 (共{len(slippage_list)}笔交易):")
            print(f"  平均滑点: {avg_slippage:.8f}")
            print(f"  最大滑点: {max_slippage:.8f}")
            print(f"  最小滑点: {min_slippage:.8f}")
            print(f"  滑点标准差: {std_slippage:.8f}")
            
            analysis_results.extend([
                f"📉 滑点分析 (共{len(slippage_list)}笔交易):",
                f"  平均滑点: {avg_slippage:.8f}",
                f"  最大滑点: {max_slippage:.8f}",
                f"  最小滑点: {min_slippage:.8f}",
                f"  滑点标准差: {std_slippage:.8f}"
            ])
            
            # 百分比滑点
            if slippage_percent_list:
                avg_pct = sum(slippage_percent_list) / len(slippage_percent_list)
                max_pct = max(slippage_percent_list)
                min_pct = min(slippage_percent_list)
                
                print(f"相对滑点百分比:")
                print(f"  平均滑点百分比: {avg_pct:.6f}%")
                print(f"  最大滑点百分比: {max_pct:.6f}%")
                print(f"  最小滑点百分比: {min_pct:.6f}%")
                
                analysis_results.extend([
                    f"相对滑点百分比:",
                    f"  平均滑点百分比: {avg_pct:.6f}%",
                    f"  最大滑点百分比: {max_pct:.6f}%",
                    f"  最小滑点百分比: {min_pct:.6f}%"
                ])
        
        # 交易延迟分析
        if trade_delay_list:
            avg_delay = sum(trade_delay_list) / len(trade_delay_list)
            max_delay = max(trade_delay_list)
            min_delay = min(trade_delay_list)
            
            print(f"交易延迟分析 (共{len(trade_delay_list)}笔交易):")
            print(f"  平均延迟: {avg_delay:.2f}ms")
            print(f"  最大延迟: {max_delay:.2f}ms")
            print(f"  最小延迟: {min_delay:.2f}ms")
            
            analysis_results.extend([
                f"⏱️ 交易延迟分析 (共{len(trade_delay_list)}笔交易):",
                f"  平均延迟: {avg_delay:.2f}ms",
                f"  最大延迟: {max_delay:.2f}ms",
                f"  最小延迟: {min_delay:.2f}ms"
            ])
        else:
            print("无交易延迟数据")
            analysis_results.append("无交易延迟数据")
        
        print(f"总成交笔数: {self.trade_count}")
        analysis_results.append(f"💹 总成交笔数: {self.trade_count}")
        
        print("=== 分析结束 ===\n")
        analysis_results.append("=== 分析结束 ===\n")
        
        # 将分析结果添加到日志
        global main_engine
        if main_engine:
            for line in analysis_results:
                log_data = LogData(
                    msg=line,
                    level=logging.INFO,
                    gateway_name="SimpleTCA"
                )
                main_engine.event_engine.put(Event(EVENT_LOG, log_data))
                
        return analysis_results

class SimpleStrategy:
    """简单策略，只读取和打印市场数据，不做复杂的交易决策"""
    
    def __init__(self, name="SimpleStrategy", symbol="btcusdt.BINANCE", main_engine=None):
        self.name = name
        self.vt_symbol = symbol
        self.pos = 0  # 当前持仓
        self.trading = True  # 是否交易
        self.inited = False  # 是否初始化完成
        
        # 添加main_engine引用，用于发送订单
        self.main_engine = main_engine
        
        # 解析交易对信息
        if "." in symbol:
            self.symbol, exchange_str = symbol.split(".")
            self.exchange = Exchange(exchange_str)
        else:
            self.symbol = symbol
            self.exchange = Exchange.BINANCE
            
        # K线数据管理器，用于暂存接收到的数据
        self.bars = []
        self.ticks = []
        
        # 控制交易信号频率
        self.last_signal_time = None
        self.signal_interval = 10  # 秒，每10秒最多一个信号
        
        # 交易参数
        self.order_volume = 0.01  # 每次交易0.01个比特币（原来是0.001）
        
        print(f"创建简单策略: {name}, 交易对: {symbol}")
    
    def on_init(self):
        """策略初始化"""
        print(f"{self.name}: 策略初始化")
        
        # 创建一个引擎适配器
        if hasattr(self, 'main_engine') and self.main_engine:
            engine_adapter = EngineAdapter(self.main_engine)
            # 加载历史K线数据
            bars = engine_adapter.load_bar(days=1)
            # 将K线数据传递给策略
            for bar in bars:
                self.on_bar(bar)
            print(f"已加载 {len(bars)} 条历史K线数据")
        
        self.inited = True
        return True
    
    def on_start(self):
        """策略启动"""
        print(f"{self.name}: 策略启动")
        return True
    
    def on_stop(self):
        """策略停止"""
        print(f"{self.name}: 策略停止")
        return True
    
    def on_tick(self, tick):
        """接收Tick数据"""
        self.ticks.append(tick)
        
        # 只有重要的tick数据才打印，减少控制台输出
        # 这里设置为每100个tick才输出一次
        if len(self.ticks) % 100 == 0:
            print(f"{self.name}: 收到第 {len(self.ticks)} 个Tick: {tick.symbol}, 价格: {tick.last_price}")
    
    def on_bar(self, bar):
        """接收K线数据"""
        # 限制打印频率，每10根K线打印一次
        should_print = len(self.bars) % 10 == 0
        
        self.bars.append(bar)
        if should_print:
            print(f"\n===> {self.name}: 收到K线: {bar.symbol}, 价格: {bar.close_price}, 时间: {bar.datetime}")
        
        # 确保至少有3条K线数据才开始生成信号
        if len(self.bars) < 3:
            if should_print:
                print(f"K线数量不足，当前: {len(self.bars)}/3 条")
            return
            
        # 控制信号频率 - 检查距离上次信号是否已经过了指定时间
        current_time = datetime.now()
        if self.last_signal_time and (current_time - self.last_signal_time).total_seconds() < self.signal_interval:
            if should_print:
                seconds_passed = (current_time - self.last_signal_time).total_seconds()
                seconds_remaining = self.signal_interval - seconds_passed
                print(f"信号间隔限制，需再等待 {seconds_remaining:.1f} 秒")
            return
        
        # 生成交易信号 - 使用更少的K线来计算平均价格
        latest_bars = self.bars[-3:]  # 只使用最近3条K线
        prices = [b.close_price for b in latest_bars]
        avg_price = sum(prices) / len(prices)
        
        # 计算价格偏离百分比
        current_price = bar.close_price
        price_diff_percent = (current_price - avg_price) / avg_price * 100
        
        # 打印每次K线的计算结果 (更频繁地打印)
        if should_print or len(self.bars) % 3 == 0:
            print(f"\n*** 价格分析 ***")
            print(f"最近3条K线均价: {avg_price:.2f}, 当前价格: {current_price:.2f}")
            print(f"价格偏离: {price_diff_percent:.6f}% (阈值: 任何非零偏离)")
            
            # 打印交易条件是否满足
            if current_price > avg_price:
                print(f"价格高于均线 ↑ (当前持仓: {self.pos})")
                if price_diff_percent > 0.00000 and self.pos <= 0:
                    print(f"✅ 买入条件满足! 价格比均线高 {current_price - avg_price:.8f}")
                else:
                    reason = "当前已有多头持仓" if self.pos > 0 else "价格偏离为0"
                    print(f"❌ 买入条件不满足: {reason}")
            else:
                print(f"价格低于均线 ↓ (当前持仓: {self.pos})")
                if price_diff_percent < 0.00000 and self.pos >= 0:
                    print(f"✅ 卖出条件满足! 价格比均线低 {avg_price - current_price:.8f}")
                else:
                    reason = "当前已有空头持仓" if self.pos < 0 else "价格偏离为0"
                    print(f"❌ 卖出条件不满足: {reason}")
        
        # 几乎所有的价格偏离都会产生信号
        if price_diff_percent > 0.00000 and self.pos <= 0:
            print(f"\n========================")
            print(f"🔴 {self.name}: 生成买入信号!!!")
            print(f"均价: {avg_price:.2f}, 当前价: {current_price:.2f}")
            print(f"偏离: +{price_diff_percent:.6f}% ({current_price - avg_price:.8f})")
            print(f"========================\n")
            
            # 实际发送买入订单
            order_id = self.send_buy_order(current_price)
            
            self.last_signal_time = current_time
            self.pos = 1  # 模拟持仓变化
                
        elif price_diff_percent < 0.00000 and self.pos >= 0:
            print(f"\n========================")
            print(f"🔵 {self.name}: 生成卖出信号!!!")
            print(f"均价: {avg_price:.2f}, 当前价: {current_price:.2f}")
            print(f"偏离: {price_diff_percent:.6f}% ({current_price - avg_price:.8f})")
            print(f"========================\n")
            
            # 实际发送卖出订单
            order_id = self.send_sell_order(current_price)
            
            self.last_signal_time = current_time
            self.pos = -1  # 模拟持仓变化

    def send_buy_order(self, price):
        """发送买入订单"""
        if not self.main_engine:
            print("⚠️ 无法发送订单: main_engine未设置")
            return
            
        # 检查资金是否充足，使用自定义持仓跟踪器
        global global_position_tracker
        if not global_position_tracker:
            print("⚠️ 无法获取自定义持仓跟踪器，取消买入")
            return
            
        required_funds = price * self.order_volume
        
        if not global_position_tracker.has_enough_balance(required_funds):
            print(f"⚠️ 资金不足: 需要 {required_funds}, 可用 {global_position_tracker.get_available_balance()}, 取消买入")
            
            # 手动记录日志到GUI
            log_data = LogData(
                msg=f"⚠️ 资金不足: 需要 {required_funds:.6f}, 可用 {global_position_tracker.get_available_balance():.6f}, 取消买入操作",
                level=logging.WARNING,
                gateway_name="SYSTEM"
            )
            self.main_engine.event_engine.put(Event(EVENT_LOG, log_data))
            return
        
        # 冻结资金
        global_position_tracker.freeze_funds(required_funds)
            
        # 创建买单
        order_req = OrderRequest(
            symbol=self.symbol.lower(),  # 注意：symbol必须小写
            exchange=self.exchange,
            direction=Direction.LONG,  # 买入
            offset=Offset.OPEN,  # 开仓
            type=OrderType.LIMIT,  # 限价单
            price=price,  # 使用传入的价格
            volume=self.order_volume,  # 使用设定的交易量
        )
        print(f"📤 发送买入订单: {self.symbol}, 价格: {price}, 数量: {self.order_volume}")
        vt_orderid = self.main_engine.send_order(order_req, self.exchange.value + "_SPOT")
        print(f"📋 订单已发送, vt_orderid: {vt_orderid}")
        
        # 使用SimpleTCA记录订单
        global global_tca
        if global_tca:
            global_tca.record_order(price, Direction.LONG, self.order_volume, vt_orderid)
        
        # 手动记录日志到GUI
        log_data = LogData(
            msg=f"📤 发送买入订单: {self.symbol}, 价格: {price:.4f}, 数量: {self.order_volume}, ID: {vt_orderid}",
            level=logging.INFO,
            gateway_name="TRADE"
        )
        self.main_engine.event_engine.put(Event(EVENT_LOG, log_data))
        
        # 记录当前资金状态到GUI
        log_data = LogData(
            msg=f"💰 当前自管账户: 余额={global_position_tracker.balance:.6f}, 冻结={global_position_tracker.frozen:.6f}, 可用={global_position_tracker.get_available_balance():.6f}",
            level=logging.INFO,
            gateway_name="ACCOUNT"
        )
        self.main_engine.event_engine.put(Event(EVENT_LOG, log_data))
        
        return vt_orderid
        
    def send_sell_order(self, price):
        """发送卖出订单"""
        if not self.main_engine:
            print("⚠️ 无法发送订单: main_engine未设置")
            return
            
        # 检查是否有持仓可卖，使用自定义持仓跟踪器
        global global_position_tracker
        
        if not global_position_tracker:
            print("⚠️ 无法获取自定义持仓跟踪器，取消卖出")
            return
            
        # 检查是否有多头持仓可平仓
        if not global_position_tracker.has_position_to_sell(self.symbol.lower(), self.order_volume):
            print(f"⚠️ 持仓不足: 需要 {self.order_volume} 的多头持仓用于卖出，取消卖出")
            
            # 手动记录日志到GUI
            log_data = LogData(
                msg=f"⚠️ 持仓不足: 无法卖出 {self.order_volume} {self.symbol}，取消卖出操作",
                level=logging.WARNING,
                gateway_name="SYSTEM"
            )
            self.main_engine.event_engine.put(Event(EVENT_LOG, log_data))
            
            # 记录当前持仓到GUI
            positions = global_position_tracker.get_all_positions()
            if positions:
                for symbol, pos in positions.items():
                    log_data = LogData(
                        msg=f"📊 当前持仓: {symbol}, 方向: {pos.get('direction')}, 数量: {pos.get('volume')}, 价格: {pos.get('price')}",
                        level=logging.INFO,
                        gateway_name="POSITION"
                    )
                    self.main_engine.event_engine.put(Event(EVENT_LOG, log_data))
            else:
                log_data = LogData(
                    msg=f"📊 当前无持仓",
                    level=logging.INFO,
                    gateway_name="POSITION"
                )
                self.main_engine.event_engine.put(Event(EVENT_LOG, log_data))
            
            return
            
        # 创建卖单
        order_req = OrderRequest(
            symbol=self.symbol.lower(),  # 注意：symbol必须小写
            exchange=self.exchange,
            direction=Direction.SHORT,  # 卖出
            offset=Offset.CLOSE,  # 平仓
            type=OrderType.LIMIT,  # 限价单
            price=price,  # 使用传入的价格
            volume=self.order_volume,  # 使用设定的交易量
        )
        print(f"📤 发送卖出订单: {self.symbol}, 价格: {price}, 数量: {self.order_volume}")
        vt_orderid = self.main_engine.send_order(order_req, self.exchange.value + "_SPOT")
        print(f"📋 订单已发送, vt_orderid: {vt_orderid}")
        
        # 使用SimpleTCA记录订单
        global global_tca
        if global_tca:
            global_tca.record_order(price, Direction.SHORT, self.order_volume, vt_orderid)
        
        # 手动记录日志到GUI
        log_data = LogData(
            msg=f"📤 发送卖出订单: {self.symbol}, 价格: {price:.4f}, 数量: {self.order_volume}, ID: {vt_orderid}",
            level=logging.INFO,
            gateway_name="TRADE"
        )
        self.main_engine.event_engine.put(Event(EVENT_LOG, log_data))
        
        # 记录当前持仓到GUI
        positions = global_position_tracker.get_all_positions()
        if positions:
            for symbol, pos in positions.items():
                log_data = LogData(
                    msg=f"📊 当前持仓: {symbol}, 方向: {pos.get('direction')}, 数量: {pos.get('volume')}, 价格: {pos.get('price')}",
                    level=logging.INFO,
                    gateway_name="POSITION"
                )
                self.main_engine.event_engine.put(Event(EVENT_LOG, log_data))
        
        return vt_orderid

def on_contract(event: Event):
    #print("Contracts loaded, gateway connected.")
    pass

def on_account(event: Event):
    """处理账户更新事件 - 仅记录日志，不影响自维护的资金管理"""
    account = event.data
    print(f"收到账户更新事件 (仅记录，不使用): ID={account.accountid}, Balance={account.balance}, Frozen={account.frozen}")
    
    # 输出自维护的资金状态作为对比
    global global_position_tracker
    if global_position_tracker:
        print(f"💰 自维护资金: 余额={global_position_tracker.balance:.6f}, 冻结={global_position_tracker.frozen:.6f}, 可用={global_position_tracker.get_available_balance():.6f}")

def on_log(event: Event):
    """处理日志事件"""
    log = event.data
    # 打印到控制台
    print(f"Log: {log.msg}")
    
    # 同时记录到文件
    log_level = log.level if hasattr(log, 'level') else logging.INFO
    logger = logging.getLogger(log.gateway_name if hasattr(log, 'gateway_name') else "VNPY")
    logger.log(log_level, f"{log.msg}")

def on_position(event: Event):
    """持仓更新事件的处理函数 - 仅记录日志，不再用于实际持仓管理"""
    position = event.data
    print(f"收到持仓事件: Symbol={position.symbol}, Direction={position.direction}, Volume={position.volume} (忽略)")

def calculate_total_assets(main_engine=None):
    """计算账户总资产（使用自维护的数据）"""
    global global_position_tracker
    
    if not global_position_tracker:
        print("错误: 持仓跟踪器未初始化")
        return 0.0
    
    # 基础资金
    total_assets = global_position_tracker.balance
    
    # 加上持仓价值
    positions = global_position_tracker.get_all_positions()
    for symbol, pos in positions.items():
        # 获取当前市场价格，或使用持仓价格
        position_value = pos['volume'] * pos['price']
        total_assets += position_value
    
    print(f"总资产估值(USDT): {total_assets:.6f}")
    return total_assets

def print_positions(main_engine):
    """打印持仓信息 - 现在只使用自定义持仓跟踪器，本函数仅作为参考"""
    print("\n=== [已弃用] 从MainEngine获取持仓信息 ===")
    print("注意: 我们现在使用自定义持仓跟踪器，不再依赖VNPy的持仓管理")
    return

def check_order_status(main_engine, vt_orderid):
    """检查订单状态"""
    active_orders = main_engine.get_all_active_orders()
    for order in active_orders:
        if order.vt_orderid == vt_orderid:
            print(f"Order {vt_orderid} status: {order.status}")
            return order
    print(f"Order {vt_orderid} not found in active orders")
    return None

def get_current_price(main_engine, symbol, gateway_name="BINANCE_SPOT"):
    """获取当前市场价格"""
    # 格式化完整的vt_symbol
    vt_symbol = f"{symbol}.{gateway_name.split('_')[0]}"
    
    # 获取最新的tick数据
    tick = main_engine.get_tick(vt_symbol)
    
    if tick:
        print(f"当前市场价格 - 最新成交价: {tick.last_price}, 买一价: {tick.bid_price_1}, 卖一价: {tick.ask_price_1}")
        return tick.ask_price_1  # 返回卖一价作为买入价格
    else:
        print(f"无法获取 {symbol} 的价格信息")
        return None

def on_order(event: Event):
    """处理订单更新事件"""
    order = event.data
    print(f"\n订单更新: {order.vt_orderid}")
    print(f"状态: {order.status}")
    print(f"交易对: {order.symbol}")
    print(f"方向: {order.direction}")
    print(f"价格: {order.price}")
    print(f"数量: {order.volume}")
    print(f"已成交: {order.traded}")
    print(f"剩余: {order.volume - order.traded}")
    print(f"创建时间: {order.datetime}")
    
    # 使用SimpleTCA记录订单
    global global_tca
    if global_tca and hasattr(order, 'price') and hasattr(order, 'direction') and hasattr(order, 'volume') and hasattr(order, 'vt_orderid'):
        global_tca.record_order(order.price, order.direction, order.volume, order.vt_orderid)
        print(f"TCA: 记录订单 {order.vt_orderid} 到SimpleTCA")

class CustomPositionTracker:
    """自定义持仓跟踪器，不依赖VNPy内部机制，同时管理资金"""
    
    def __init__(self, save_path="custom_positions.json", initial_balance=10000.0):
        self.positions = {}  # symbol -> {volume, direction, price}
        self.save_path = Path(save_path).absolute()
        self.balance = initial_balance  # 设置初始资金为10000
        self.frozen = 0.0  # 冻结资金
        print(f"持仓数据文件路径: {self.save_path}")
        print(f"初始资金: {self.balance}")
        self.load_positions()
        
    def load_positions(self):
        """从文件加载持仓数据和资金数据"""
        if self.save_path.exists():
            try:
                with open(self.save_path, "r") as f:
                    loaded_data = json.load(f)
                    # 检查是否有新格式数据
                    if "positions" in loaded_data and "balance" in loaded_data:
                        self.positions = loaded_data["positions"]
                        self.balance = loaded_data["balance"]
                        self.frozen = loaded_data.get("frozen", 0.0)
                    else:
                        # 兼容旧格式
                        self.positions = loaded_data
                print(f"从 {self.save_path} 加载了持仓数据:")
                for symbol, pos in self.positions.items():
                    print(f"  - {symbol}: 方向={pos.get('direction', 'None')}, 数量={pos.get('volume', 0)}, 价格={pos.get('price', 0)}")
                print(f"当前资金: 余额={self.balance}, 冻结={self.frozen}, 可用={self.balance - self.frozen}")
            except Exception as e:
                print(f"加载持仓数据出错: {e}")
                self.positions = {}
        else:
            print(f"持仓数据文件 {self.save_path} 不存在，将创建新文件")
            self.positions = {}
    
    def save_positions(self):
        """保存持仓数据和资金数据到文件"""
        try:
            # 新格式保存，包含资金信息
            save_data = {
                "positions": self.positions,
                "balance": self.balance,
                "frozen": self.frozen,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            with open(self.save_path, "w") as f:
                json.dump(save_data, f, indent=4)
            print(f"持仓和资金数据已保存到 {self.save_path}")
        except Exception as e:
            print(f"保存持仓数据出错: {e}")
    
    def get_available_balance(self):
        """获取可用资金"""
        return self.balance - self.frozen
    
    def freeze_funds(self, amount):
        """冻结资金，用于挂单"""
        available = self.get_available_balance()
        if amount > available:
            return False
        
        self.frozen += amount
        self.save_positions()
        return True
    
    def unfreeze_funds(self, amount):
        """解冻资金，用于撤单或部分成交"""
        self.frozen = max(0, self.frozen - amount)
        self.save_positions()
        return True
    
    def update_balance(self, change_amount, reason="交易"):
        """更新资金余额"""
        old_balance = self.balance
        self.balance += change_amount
        self.save_positions()
        
        balance_msg = f"资金更新 - {reason}: {old_balance:.6f} -> {self.balance:.6f} (变化: {change_amount:+.6f})"
        print(balance_msg)
        logging.info(balance_msg)
        
        # 向GUI发送资金变化日志
        global main_engine
        if main_engine:
            log_data = LogData(
                msg=f"💰 资金更新 - {reason}: 余额={self.balance:.6f}, 冻结={self.frozen:.6f}, 可用={self.get_available_balance():.6f} (变化: {change_amount:+.6f})",
                level=logging.INFO,
                gateway_name="ACCOUNT"
            )
            main_engine.event_engine.put(Event(EVENT_LOG, log_data))
        
        return True
    
    def update_from_trade(self, trade):
        """根据成交记录更新持仓和资金"""
        symbol = trade.symbol
        direction = trade.direction.value  # '多' 或 '空'
        price = trade.price
        volume = trade.volume
        
        # 记录交易到日志文件
        trade_log = f"交易执行: {symbol}, 方向={direction}, 价格={price:.4f}, 数量={volume:.6f}, 成交金额={price*volume:.6f}"
        logging.info(trade_log)
        
        # 更新前持仓状态
        old_position = self.positions.get(symbol, {}).copy() if symbol in self.positions else None
        
        # 更新持仓
        if symbol not in self.positions:
            self.positions[symbol] = {
                "volume": 0.0,
                "direction": None,
                "price": 0.0
            }
        
        pos = self.positions[symbol]
        trade_value = price * volume  # 成交金额
        
        if direction == "多":
            # 买入，资金减少
            self.unfreeze_funds(trade_value)  # 解冻相应资金
            self.update_balance(-trade_value, f"买入 {symbol}")  # 资金减少
            
            # 持仓更新
            if pos["direction"] is None or pos["direction"] == "多":
                # 新开仓或加仓
                new_volume = pos["volume"] + volume
                new_price = (pos["price"] * pos["volume"] + price * volume) / new_volume if new_volume > 0 else price
                pos["volume"] = new_volume
                pos["price"] = new_price
                pos["direction"] = "多"
            else:
                # 空头减仓
                pos["volume"] -= volume
                if pos["volume"] <= 0:
                    # 如果平仓后变为多头
                    pos["volume"] = abs(pos["volume"])
                    pos["direction"] = "多" if pos["volume"] > 0 else None
                    pos["price"] = price
        else:
            # 卖出，资金增加
            self.update_balance(trade_value, f"卖出 {symbol}")  # 资金增加
            
            # 持仓更新
            if pos["direction"] is None or pos["direction"] == "空":
                # 新开空仓或加空仓
                new_volume = pos["volume"] + volume
                new_price = (pos["price"] * pos["volume"] + price * volume) / new_volume if new_volume > 0 else price
                pos["volume"] = new_volume
                pos["price"] = price
                pos["direction"] = "空"
            else:
                # 多头减仓
                pos["volume"] -= volume
                if pos["volume"] <= 0:
                    # 如果平仓后变为空头
                    pos["volume"] = abs(pos["volume"])
                    pos["direction"] = "空" if pos["volume"] > 0 else None
                    pos["price"] = price
        
        # 如果持仓量为0，删除该持仓
        if pos["volume"] == 0:
            del self.positions[symbol]
        
        # 保存更新后的持仓
        self.save_positions()
        print(f"持仓已更新 - {symbol}: 方向={pos.get('direction')}, 数量={pos.get('volume')}, 价格={pos.get('price')}")
        
        # 向GUI发送持仓变化日志
        global main_engine
        if main_engine:
            # 计算持仓变化
            change_msg = ""
            if old_position:
                old_dir = old_position.get("direction", "无")
                old_vol = old_position.get("volume", 0)
                old_price = old_position.get("price", 0)
                
                if symbol in self.positions:
                    new_dir = pos.get("direction", "无")
                    new_vol = pos.get("volume", 0)
                    new_price = pos.get("price", 0)
                    
                    if old_dir != new_dir:
                        change_msg = f"方向变化: {old_dir} → {new_dir}, "
                    
                    vol_change = new_vol - old_vol
                    if vol_change != 0:
                        change_msg += f"数量变化: {old_vol:.6f} → {new_vol:.6f} ({'+' if vol_change > 0 else ''}{vol_change:.6f}), "
                    
                    price_change = new_price - old_price
                    if price_change != 0:
                        change_msg += f"价格变化: {old_price:.4f} → {new_price:.4f} ({'+' if price_change > 0 else ''}{price_change:.4f})"
                else:
                    # 持仓被平仓
                    change_msg = f"持仓已平仓: 原方向={old_dir}, 原数量={old_vol:.6f}, 原价格={old_price:.4f}"
            else:
                # 新建持仓
                if symbol in self.positions:
                    change_msg = f"新建持仓: 方向={pos.get('direction')}, 数量={pos.get('volume'):.6f}, 价格={pos.get('price'):.4f}"
            
            # 发送持仓变化日志
            if change_msg:
                log_data = LogData(
                    msg=f"📊 持仓更新 - {symbol}: {change_msg}",
                    level=logging.INFO,
                    gateway_name="POSITION"
                )
                main_engine.event_engine.put(Event(EVENT_LOG, log_data))
            
            # 发送当前所有持仓的汇总信息
            position_summary = "无持仓"
            if self.positions:
                position_list = []
                for sym, p in self.positions.items():
                    position_list.append(f"{sym}: {p.get('direction', '无')}/{p.get('volume', 0):.6f}/{p.get('price', 0):.2f}")
                position_summary = ", ".join(position_list)
                
            log_data = LogData(
                msg=f"📈 持仓汇总: {position_summary}",
                level=logging.INFO,
                gateway_name="SUMMARY"
            )
            main_engine.event_engine.put(Event(EVENT_LOG, log_data))
    
    def get_all_positions(self):
        """获取所有持仓"""
        return self.positions
        
    def has_position_to_sell(self, symbol, volume):
        """检查是否有足够的多头持仓可卖"""
        if symbol not in self.positions:
            return False
            
        pos = self.positions[symbol]
        return pos.get("direction") == "多" and pos.get("volume", 0) >= volume
        
    def has_enough_balance(self, amount):
        """检查是否有足够的资金可用"""
        return self.get_available_balance() >= amount

# 添加一个简单的引擎适配器类
class EngineAdapter:
    """引擎适配器，用于连接MainEngine和Strategy"""
    def __init__(self, main_engine):
        self.main_engine = main_engine
        self.event_engine = main_engine.event_engine
        self.gateway_name = "BINANCE_SPOT"
        self.capital = 100000  # 设置初始资金
        self.strategy_orderid_map = {}  # vt_orderid -> strategy
        self.strategy_order_map = {}  # strategy -> set of vt_orderids
        self.active_orderids = set()  # active vt_orderids
        
        # 添加策略映射和订阅信息
        self.strategies = {}  # strategy_name -> strategy
        self.subscribed_symbols = {}  # strategy_name -> set of vt_symbols
        
        # 使用全局持仓跟踪器
        global global_position_tracker
        self.position_tracker = global_position_tracker
        
    def write_log(self, msg, strategy=None):
        """记录日志，兼容CtaTemplate的接口"""
        strategy_name = strategy.strategy_name if strategy else "未知策略"
        print(f"[{strategy_name}] {msg}")
        
        # 如果有主引擎，也发送日志事件
        if self.main_engine:
            log_data = LogData(
                msg=msg,
                level=logging.INFO,
                gateway_name=self.gateway_name
            )
            event = Event(EVENT_LOG, log_data)
            self.event_engine.put(event)
        
    def send_order(self, strategy, direction, offset, price, volume, stop=False, lock=False, net=False):
        """发送委托"""
        print(f"发送委托：{strategy.vt_symbol} {direction} {offset} {price} {volume}")
        
        vt_symbol = strategy.vt_symbol
        contract = self.main_engine.get_contract(vt_symbol)
        if not contract:
            print(f"找不到合约：{vt_symbol}")
            return ""
        
        req = OrderRequest(
            symbol=contract.symbol,
            exchange=contract.exchange,
            direction=direction,
            type=OrderType.LIMIT,  # 默认限价单
            volume=float(volume),
            price=float(price),
            offset=offset,
            reference=f"{strategy.strategy_name}"
        )
        
        vt_orderid = self.main_engine.send_order(req, contract.gateway_name)
        
        # 记录策略委托
        if vt_orderid:
            self.strategy_orderid_map[vt_orderid] = strategy
            
            # 添加到策略委托列表
            if strategy not in self.strategy_order_map:
                self.strategy_order_map[strategy] = set()
            self.strategy_order_map[strategy].add(vt_orderid)
            
            # 添加到所有委托集合中
            self.active_orderids.add(vt_orderid)
            
            print(f"委托发送成功，vt_orderid：{vt_orderid}")
        else:
            print(f"委托发送失败")
        
        return vt_orderid

    def cancel_order(self, strategy, vt_orderid):
        """撤销委托"""
        print(f"撤销委托：{vt_orderid}")
        
        order = self.main_engine.get_order(vt_orderid)
        if not order:
            print(f"找不到委托：{vt_orderid}")
            return
        
        req = CancelRequest(
            symbol=order.symbol,
            exchange=order.exchange,
            orderid=order.orderid,
            reference=f"{strategy.strategy_name}"
        )
        
        self.main_engine.cancel_order(req, order.gateway_name)

    def get_pricetick(self, strategy):
        """获取价格跳动"""
        contract = self.main_engine.get_contract(strategy.vt_symbol)
        if contract:
            return contract.pricetick
        else:
            return 0.00001  # 默认值

    def send_email(self, msg):
        """发送邮件通知"""
        print(f"发送邮件通知：{msg}")
        # 实际邮件发送逻辑可根据需要添加

    def get_contract(self, vt_symbol):
        """获取合约信息"""
        return self.main_engine.get_contract(vt_symbol)
    
    def get_account(self):
        """获取账户信息 - 使用自维护的账户数据"""
        global global_position_tracker
        if global_position_tracker:
            # 创建一个简单的账户对象，只包含基本信息
            class SimpleAccount:
                def __init__(self):
                    self.balance = global_position_tracker.balance
                    self.frozen = global_position_tracker.frozen
                    self.available = global_position_tracker.get_available_balance()
                    self.accountid = "CUSTOM"
                    
            return SimpleAccount()
        return None
    
    def get_tick(self, vt_symbol):
        """获取最新行情"""
        return self.main_engine.get_tick(vt_symbol)
    
    def subscribe(self, vt_symbol):
        """订阅行情，兼容多种调用方式"""
        # 如果传入的是策略实例，则获取其vt_symbol
        if hasattr(vt_symbol, 'vt_symbol'):
            vt_symbol = vt_symbol.vt_symbol
            
        if not vt_symbol:
            print("错误: 没有指定要订阅的交易对")
            return
            
        print(f"订阅 {vt_symbol} 的行情")
        
        # 解析交易对和交易所
        if "." in vt_symbol:
            symbol, exchange_str = vt_symbol.split(".")
            exchange = Exchange(exchange_str)
        else:
            symbol = vt_symbol
            exchange = Exchange.BINANCE
            
        # 创建订阅请求
        req = SubscribeRequest(
            symbol=symbol.lower(),  # 确保小写
            exchange=exchange
        )
        
        # 发送订阅请求
        if self.main_engine:
            # 获取合适的网关名称
            gateway_name = self.gateway_name
            self.main_engine.subscribe(req, gateway_name)
            print(f"已通过 {gateway_name} 网关发送订阅请求")
        else:
            print("错误: 主引擎未初始化，无法订阅行情")

    def update_bar(self, bar):
        """更新K线数据到策略"""
        if not hasattr(self, 'strategy') or not self.strategy:
            print("警告: 没有绑定策略实例，无法更新K线")
            return
            
        try:
            self.strategy.on_bar(bar)
        except Exception as e:
            print(f"策略处理K线时出错: {e}")
            import traceback
            traceback.print_exc()

    def load_bar(self, days, interval=Interval.DAILY, callback=None):
        """加载历史K线数据，生成模拟数据"""
        print(f"正在加载 {days} 天历史K线数据，周期: {interval.value}...")
        
        # 实际情况中应该从数据库中加载历史数据
        # 这里生成模拟数据进行演示
        current_time = datetime.now()
        end_time = current_time
        start_time = current_time - timedelta(days=days)
        
        bars = []
        # 根据不同的时间周期生成不同数量的K线
        if interval == Interval.DAILY:
            # 生成每日K线
            step = timedelta(days=1)
            bar_time = start_time
            while bar_time <= end_time:
                # 为每个K线生成随机数据
                bar = self._create_bar("btcusdt.BINANCE", bar_time, interval)
                bars.append(bar)
                
                # 如果有回调函数，则执行回调
                if callback:
                    callback(bar)
                    
                bar_time += step
        
        # 可以添加其他时间周期的处理逻辑
        elif interval == Interval.MINUTE:
            # 生成分钟K线，这里简化为只生成最近的30条
            for i in range(30):
                bar_time = end_time - timedelta(minutes=30-i)
                bar = self._create_bar("btcusdt.BINANCE", bar_time, interval)
                bars.append(bar)
                
                if callback:
                    callback(bar)
        
        print(f"已加载 {len(bars)} 条 {interval.value} K线数据")
        return bars
    
    def _create_bar(self, vt_symbol, bar_time, interval):
        """生成一个随机的K线数据"""
        # 解析交易对和交易所
        if "." in vt_symbol:
            symbol, exchange_str = vt_symbol.split(".")
            exchange = Exchange(exchange_str)
        else:
            symbol = vt_symbol
            exchange = Exchange.BINANCE
        
        # 根据间隔调整随机范围
        if interval == Interval.DAILY:
            # 日线波动略大
            base_price = 100.0
            open_price = base_price + random.uniform(-5, 5)
            high_price = open_price + random.uniform(0, 5)
            low_price = open_price - random.uniform(0, 5)
            close_price = (open_price + high_price + low_price) / 3 + random.uniform(-2, 2)
            volume = 1000.0 + random.uniform(-200, 200)
            turnover = close_price * volume
        else:
            # 分钟线波动小
            base_price = 100.0
            open_price = base_price + random.uniform(-2, 2)
            high_price = open_price + random.uniform(0, 2)
            low_price = open_price - random.uniform(0, 2)
            close_price = (open_price + high_price + low_price) / 3 + random.uniform(-1, 1)
            volume = 100.0 + random.uniform(-20, 20)
            turnover = close_price * volume
            
        # 创建并返回K线对象
        return BarData(
            symbol=symbol,
            exchange=exchange,
            datetime=bar_time,
            interval=interval,
            open_price=open_price,
            high_price=max(high_price, open_price, close_price),
            low_price=min(low_price, open_price, close_price),
            close_price=close_price,
            volume=volume,
            turnover=turnover,
            gateway_name=self.gateway_name
        )

    def load_tick(self, days, callback=None):
        """加载历史Tick数据，生成模拟数据"""
        print(f"正在加载 {days} 天历史Tick数据...")
        
        # 实际情况中应该从数据库中加载历史数据
        # 这里生成模拟数据进行演示
        current_time = datetime.now()
        end_time = current_time
        start_time = current_time - timedelta(days=days)
        
        # 硬编码常用的交易对信息
        symbol = "btcusdt"
        exchange = Exchange.BINANCE
            
        ticks = []
        # 简化为生成最近的100个tick
        for i in range(100):
            tick_time = end_time - timedelta(seconds=100-i)
            
            # 生成随机价格
            base_price = 100.0
            last_price = base_price + random.uniform(-1, 1)
            bid_price = last_price - random.uniform(0.1, 0.5)
            ask_price = last_price + random.uniform(0.1, 0.5)
            
            # 显式添加类型注释
            tick: TickData = TickData(
                symbol=symbol,
                exchange=exchange,
                datetime=tick_time,
                name=f"{symbol}",
                last_price=last_price,
                high_price=last_price + random.uniform(0, 1),
                low_price=last_price - random.uniform(0, 1),
                volume=10.0 + random.uniform(-2, 2),
                turnover=last_price * 10.0,
                open_interest=0,
                bid_price_1=bid_price,
                bid_volume_1=5.0 + random.uniform(-1, 1),
                ask_price_1=ask_price,
                ask_volume_1=5.0 + random.uniform(-1, 1),
                gateway_name=self.gateway_name
            )
            ticks.append(tick)
            
            # 如果有回调函数，则执行回调
            if callback:
                callback(tick)
        
        print(f"已加载 {len(ticks)} 条Tick数据")
        return ticks

    def get_engine_type(self):
        """返回引擎类型"""
        # 为了兼容性，返回一个假的引擎类型
        return "LiveAdapter"

    def on_order(self, order: OrderData) -> None:
        """订单更新推送"""
        print(f"收到委托回报：{order}")
        
        # 使用SimpleTCA记录订单
        global global_tca
        if global_tca and hasattr(order, 'price') and hasattr(order, 'direction') and hasattr(order, 'volume') and hasattr(order, 'vt_orderid'):
            global_tca.record_order(order.price, order.direction, order.volume, order.vt_orderid)
            print(f"TCA: 记录订单 {order.vt_orderid} 到SimpleTCA")
            
        # 更新活跃订单状态
        if order.status in [Status.ALLTRADED, Status.CANCELLED, Status.REJECTED, Status.EXPIRED]:
            if order.vt_orderid in self.active_orders:
                self.active_orders.remove(order.vt_orderid)
        
        # 查找对应的策略并推送订单更新
        for strategy_name, strategy_orders in self.strategy_orders.items():
            if order.vt_orderid in strategy_orders:
                strategy = self.strategies.get(strategy_name)
                if strategy:
                    strategy.on_order(order)
                break

    def on_trade(self, trade: TradeData) -> None:
        """成交推送"""
        print(f"收到成交回报：{trade}")
        
        # 使用SimpleTCA记录成交
        global global_tca
        if global_tca and hasattr(trade, 'price') and hasattr(trade, 'direction') and hasattr(trade, 'volume') and hasattr(trade, 'vt_orderid'):
            # 检查是否有对应的订单记录
            order_found = False
            for order in global_tca.order_records:
                if order["order_id"] == trade.vt_orderid:
                    order_found = True
                    break
                    
            if not order_found:
                # 如果没有找到订单记录，先添加一个
                global_tca.record_order(trade.price, trade.direction, trade.volume, trade.vt_orderid)
                print(f"TCA: 补充订单记录 {trade.vt_orderid}")
            
            # 记录成交
            global_tca.record_trade(trade.price, trade.direction, trade.volume, trade.vt_orderid)
            
        # 更新自定义持仓
        if self.position_tracker:
            self.position_tracker.update_from_trade(trade)
        
        # 查找对应的策略并推送成交更新
        for strategy_name, strategy_orders in self.strategy_orders.items():
            if trade.vt_orderid in strategy_orders:
                strategy = self.strategies.get(strategy_name)
                if strategy:
                    strategy.on_trade(trade)
                break
        
    def on_tick(self, tick: TickData) -> None:
        """行情推送"""
        # SimpleTCA不需要行情数据，不再调用update_tick
        # 行情数据仅用于策略交易决策
        
        # 遍历所有策略，根据订阅的合约推送行情
        vt_symbol = tick.vt_symbol
        for strategy_name, strategy in self.strategies.items():
            if vt_symbol in self.subscribed_symbols.get(strategy_name, set()):
                strategy.on_tick(tick)

def setup_strategy(main_engine, position_tracker):
    """设置简单策略"""
    print("\n=== 设置简单策略 ===")
    
    # 直接创建简单策略
    strategy = SimpleStrategy(name="SimpleStrategy", symbol="btcusdt.BINANCE", main_engine=main_engine)
    
    try:
        # 初始化并启动策略
        strategy.on_init()
        print("策略初始化完成")
        
        strategy.on_start()
        print("策略已启动")
        
        return strategy
    except Exception as e:
        print(f"设置策略时出错: {e}")
        import traceback
        traceback.print_exc()
        return None

def on_trade_custom(event):
    """处理成交事件，更新自定义持仓跟踪器"""
    global global_position_tracker, global_tca, main_engine
    
    if global_position_tracker is None:
        print("警告：持仓跟踪器未初始化")
        return
        
    trade = event.data
    print(f"成交事件: {trade.symbol}, {trade.direction}, 数量: {trade.volume}, 价格: {trade.price}")
    
    # 使用SimpleTCA记录成交，如果有对应订单的话
    if global_tca and hasattr(trade, 'vt_orderid'):
        # 检查SimpleTCA中是否已有此订单记录，如果没有，先添加
        order_found = False
        for order in global_tca.order_records:
            if order["order_id"] == trade.vt_orderid:
                order_found = True
                break
                
        if not order_found:
            # 如果没有找到订单记录，先添加一个
            global_tca.record_order(trade.price, trade.direction, trade.volume, trade.vt_orderid)
            print(f"TCA: 补充订单记录 {trade.vt_orderid}")
        
        # 记录成交
        global_tca.record_trade(trade.price, trade.direction, trade.volume, trade.vt_orderid)
    
    # 使用自定义持仓跟踪器更新持仓
    global_position_tracker.update_from_trade(trade)
    
    # 向GUI发送成交信息日志
    if main_engine:
        log_data = LogData(
            msg=f"✅ 成交确认: {trade.symbol}, 方向: {trade.direction.value}, 数量: {trade.volume}, 价格: {trade.price:.4f}, 时间: {trade.datetime}",
            level=logging.INFO,
            gateway_name="TRADE"
        )
        main_engine.event_engine.put(Event(EVENT_LOG, log_data))
    
    # 显示当前所有持仓
    print("\n=== 自定义持仓跟踪器 - 当前持仓 ===")
    positions = global_position_tracker.get_all_positions()
    if positions:
        for symbol, pos in positions.items():
            print(f"持仓: {symbol}, 方向: {pos['direction']}, 数量: {pos['volume']}, 价格: {pos['price']}")
    else:
        print("当前没有持仓")
    
    # 显示当前资金
    print(f"当前资金: 余额={global_position_tracker.balance:.6f}, 冻结={global_position_tracker.frozen:.6f}, 可用={global_position_tracker.get_available_balance():.6f}")

def on_tick(event, strategy_instance):
    """行情更新事件处理函数"""
    try:
        if not strategy_instance:
            return
            
        tick: TickData = event.data
        
        # SimpleTCA不需要行情数据，我们不再调用update_tick
        # 行情数据只用于策略的交易决策
        
        # 检查是否是我们关注的交易对
        strategy_symbol = strategy_instance.vt_symbol if hasattr(strategy_instance, 'vt_symbol') else ""
        tick_symbol = tick.vt_symbol if hasattr(tick, 'vt_symbol') else f"{tick.symbol}.{tick.exchange.value}"
        
        # 如果不关注此交易对，返回
        if strategy_symbol and strategy_symbol.lower() != tick_symbol.lower():
            return
            
        # 直接调用策略的on_tick方法
        strategy_instance.on_tick(tick)
        
        # 每5个tick就创建一次K线，控制K线生成频率
        if len(strategy_instance.ticks) % 5 == 0:
            # 创建一个简单的分钟K线并传给策略
            bar = BarData(
                symbol=tick.symbol,
                exchange=tick.exchange,
                datetime=tick.datetime,
                interval=Interval.MINUTE,
                open_price=tick.last_price,
                high_price=tick.last_price,
                low_price=tick.last_price,
                close_price=tick.last_price,
                volume=tick.volume,
                turnover=tick.turnover if hasattr(tick, 'turnover') else tick.last_price * tick.volume,
                gateway_name=tick.gateway_name
            )
            
            # 调用策略的on_bar方法
            strategy_instance.on_bar(bar)
        
    except Exception as e:
        print(f"处理行情时出错: {e}")
        import traceback
        traceback.print_exc()

# 策略运行函数，将在单独的线程中执行
def run_strategy_thread(strategy, global_position_tracker):
    """在单独的线程中运行策略"""
    global strategy_running
    
    print("\n策略线程启动，将持续监控市场并执行交易信号...")
    print("关闭主窗口或按 Ctrl+C 可以中断程序")
    
    # 保持线程运行，并定期检查持仓情况
    counter = 0
    while strategy_running:
        # 短暂暂停，减少CPU使用
        time.sleep(0.5)
        
        counter += 1
        
        # 每300次循环(约2.5分钟)显示一次持仓情况
        if counter % 300 == 0:
            print("\n=== 当前持仓情况 ===")
            positions = global_position_tracker.get_all_positions()
            if positions:
                for symbol, pos in positions.items():
                    print(f"持仓: {symbol}, 方向: {pos['direction']}, 数量: {pos['volume']}, 价格: {pos['price']}")
            else:
                print("当前没有持仓")
    
    # 线程结束，停止策略
    if strategy:
        strategy.on_stop()
        print("策略已停止")
    
    print("策略线程已退出")

def main():
    """主程序入口"""
    logging.info(f"{'='*30} 交易会话开始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {'='*30}")
    
    # 初始化全局变量
    global global_tca, main_engine, global_position_tracker
    
    # 创建持仓跟踪器
    global_position_tracker = CustomPositionTracker()
    
    # 创建全局的TCA分析器 - 使用新的SimpleTCA
    global_tca = SimpleTCA(analysis_interval=5)  # 每5笔交易分析一次
    
    # 显示初始资金和持仓状态
    print("\n=== 初始资金和持仓状态 ===")
    print(f"当前资金: 余额={global_position_tracker.balance:.6f}, 冻结={global_position_tracker.frozen:.6f}, 可用={global_position_tracker.get_available_balance():.6f}")
    
    positions = global_position_tracker.get_all_positions()
    if positions:
        print("当前持仓:")
        for symbol, pos in positions.items():
            print(f"  - {symbol}: 方向={pos['direction']}, 数量={pos['volume']}, 价格={pos['price']}")
    else:
        print("当前没有持仓")
    
    # 创建Qt应用
    qapp = create_qapp()
    
    # 创建主引擎
    main_engine = MainEngine()

    # 添加币安现货网关
    print("Adding Binance Spot Gateway...")
    main_engine.add_gateway(BinanceSpotGateway)
    print("Gateways:", main_engine.gateways)

    # 创建主窗口
    main_window = MainWindow(main_engine, event_engine=main_engine.event_engine)
    
    # 显示界面
    main_window.showMaximized()  # 最大化显示主窗口
    
    # 确保交易控件可见
    if hasattr(main_window, 'trading_widget'):
        main_window.trading_widget.hide()  # 隐藏交易控件

    # 注册事件监听器
    main_engine.event_engine.register(EVENT_CONTRACT, on_contract)
    main_engine.event_engine.register(EVENT_ACCOUNT, on_account)
    main_engine.event_engine.register(EVENT_LOG, on_log)
    main_engine.event_engine.register(EVENT_POSITION, on_position)
    main_engine.event_engine.register(EVENT_TRADE, on_trade_custom)
    main_engine.event_engine.register(EVENT_ORDER, on_order)

    # 连接币安现货网
    setting = {
        "API Key": "Nd8HCepr3NNbEdS2yJ6onM7hcWagiFVdghqcMQ3Uj6JHIlwOIGhEa79ribf0fuIK",  # 替换为你的 API Key
        "API Secret": "f74YLBJfU4K1oDd59FQI4IhNfs1dwkZywEv4MBq60rCjienn2w91pnCv5O6d6rXL",  # 替换为你的 API Secret
        "Server": "TESTNET",  # 确保设置为 TESTNET 或 REAL
        "Kline Stream": "True",  # 新增字段
        "Proxy Host": "",
        "Proxy Port": 0
    }
    print("Connecting to Binance Spot...")
    main_engine.connect(setting, "BINANCE_SPOT")
    print("Waiting for connection to establish...")
    time.sleep(5)  # 等待 5 秒，确保连接完成
    
    # 检查网关是否加载
    gateway = main_engine.get_gateway("BINANCE_SPOT")
    if gateway:
        print("Gateway loaded successfully.")
    else:
        print("Failed to load gateway.")
        return

    # 设置并启动策略
    print("Setting up trading strategy...")
    strategy = setup_strategy(main_engine, global_position_tracker)
    
    if not strategy:
        print("警告: 策略初始化失败，程序将不会运行策略。")
        print("程序将继续运行，但不会生成交易信号。")
    else:
        print(f"策略 {strategy.name} 已成功启动")
        # 注册TICK事件，将行情数据传递给策略
        main_engine.event_engine.register(EVENT_TICK, lambda event: on_tick(event, strategy))
    
    # 订阅交易对
    if strategy:
        # 直接使用SimpleStrategy的vt_symbol
        symbol, exchange_str = strategy.vt_symbol.split(".")
        exchange = Exchange(exchange_str)
        print(f"订阅 {strategy.vt_symbol} 行情...")
    else:
        # 如果策略初始化失败，使用默认设置
        symbol = "btcusdt"
        exchange = Exchange.BINANCE
        print(f"订阅 {symbol.upper()} 行情...")
        
    # 创建并发送订阅请求
    subscribe_req = SubscribeRequest(
        symbol=symbol.lower(),
        exchange=exchange
    )
    main_engine.subscribe(subscribe_req, "BINANCE_SPOT")
    print("订阅请求已发送")
    
    # 在单独的线程中运行策略
    global strategy_running
    strategy_running = True
    strategy_thread = threading.Thread(
        target=run_strategy_thread, 
        args=(strategy, global_position_tracker),
        daemon=True  # 设置为守护线程，这样主程序退出时线程也会退出
    )
    strategy_thread.start()
    
    # 开始事件循环前，为主窗口关闭添加处理
    def on_main_window_closed():
        global strategy_running
        strategy_running = False
        print("主窗口关闭，正在停止策略...")
        # 等待策略线程结束
        if strategy_thread.is_alive():
            strategy_thread.join(timeout=2.0)
        # 显示最终持仓
        print("\n=== 程序结束前 - 自定义持仓跟踪器 - 当前持仓 ===")
        positions = global_position_tracker.get_all_positions()
        if positions:
            for symbol, pos in positions.items():
                print(f"持仓: {symbol}, 方向: {pos['direction']}, 数量: {pos['volume']}, 价格: {pos['price']}")
        else:
            print("当前没有持仓")
        
        # 保存最终持仓和资金状态
        global_position_tracker.save_positions()
        print(f"最终资金状态：余额={global_position_tracker.balance:.6f}, 可用={global_position_tracker.get_available_balance():.6f}")
        
        # 记录会话结束
        session_end_msg = f"\n{'='*80}\n{' '*30}会话结束: {datetime.now()}\n{'='*80}"
        print(session_end_msg)
        logging.info(session_end_msg)
    
    # 连接窗口关闭信号
    main_window.closeEvent = lambda event: on_main_window_closed()

    # 启动Qt事件循环
    sys.exit(qapp.exec())

if __name__ == "__main__":
    main()
