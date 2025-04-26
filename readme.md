# 基于 VnPy 的量化交易系统

本项目旨在开发一个基于 VnPy 的量化交易系统，支持加密货币交易，具备完整的策略回测、实盘交易功能。

## 技术栈

- **环境管理**：Anaconda
- **编程语言**：Python 3.10
- **量化框架**：VnPy 3.9.4
- **交易所接口**：ccxt、vnpy-binance
- **数据库**：MongoDB
- **数据分析与可视化**：pandas、numpy、matplotlib、seaborn
- **界面**：修改VnTrader主界面
- **其他依赖**：见 requirements.txt

## 系统架构

- **数据获取**：使用 ccxt 接口获取历史数据和实时行情
- **数据存储**：使用 MongoDB 数据库存储历史数据
- **回测引擎**：基于 VnPy 的回测系统
- **交易执行**：对接币安模拟交易接口，支持现货交易
- **持仓管理**：自定义持仓跟踪器，独立于交易所API
- **TCA 分析**：集成交易成本分析，监控滑点和交易延迟
- **界面展示**：基于 VnTrader 的界面，将手动下单页面改为交易监控页面，进行策略监控和日志展示

## 主要功能

1. **数据模块**
   - 历史数据获取与存储
   - 数据清洗与预处理,过滤错误数据

2. **回测模块**
   - 历史数据回测
   - 性能评估指标(Sharpe比率、最大回撤等)
   - 记录回测日志
   - 生成回测结果图

3. **持仓管理**
   - 自定义持仓跟踪器
   - 资金冻结和解冻
   - 交易记录和持仓更新
   - 持仓数据持久化

4. **界面与监控**
   - 修改VnTrader主界面进行策略运行、日志、持仓等信息展示

## 环境配置

推荐使用 Anaconda 管理 Python 环境：

```bash
# 创建新环境
conda create -n vnpy_crypto python=3.9
conda activate vnpy_crypto

# 安装依赖
pip install -r requirements.txt

```

## 快速开始

1. **配置环境**
   ```bash
   # 安装依赖
   pip install -r requirements.txt

2. **配置币安API**
   ```python
   # config/config.json 中填入API信息
   {
       "binance": {
           "api_key": "your_api_key",
           "api_secret": "your_secret_key"
       },
       "database": {
           "driver": "mongodb",
           "host": "localhost",
           "port": 27017,
           "database": "crypto_trading"
       }
   }
   ```

3. **获取历史数据**
   ```bash
   python fetch_btc_data.py
   ```

4. **运行回测**
   ```bash
   python run_backtest.py
   ```

5. **启动实盘交易**
   ```bash
   python run_live_trading.py
   ```

6.

## 项目结构

```
├── config/                 # 配置文件目录
│   └── config.json         # 主配置文件
├── logs/                   # 日志文件目录
│   ├── backtest.log        # 回测日志
│   └── trading_log.log     # 交易日志
├── src/                    # 源代码目录
│   ├── strategies/         # 交易策略实现
│   │   └── trading_strategy.py  # 中频交易策略
│   ├── backtest/           # 回测引擎
│   │   └── backtest_engine.py   # 回测引擎实现
│   ├── data/               # 数据获取与处理
│   │   └── data_fetcher.py      # 数据获取器
│   └── __init__.py
├── fetch_btc_data.py       # 获取比特币数据
├── requirements.txt        # 依赖包列表
├── run_backtest.py         # 运行回测脚本
├── run_live_trading.py     # 运行实盘交易脚本
└── custom_positions.json   # 自定义持仓记录
```

## 使用说明

### 1. 数据获取

数据获取模块支持从币安获取历史K线数据，并存储到MongoDB中。

```python
from src.data.data_fetcher import DataFetcher

# 初始化数据获取器
fetcher = DataFetcher()

# 获取比特币数据
data = fetcher.fetch_history(
    symbol="BTCUSDT",
    interval="1m",
    start_time="2024-01-01",
    end_time="2024-12-31"
)

# 保存到数据库
fetcher.save_to_database(data, "BTCUSDT", "1m")
```

### 2. 回测运行

回测模块支持对策略进行历史数据回测和性能评估。

```python
from src.backtest.backtest_engine import BacktestEngine
from datetime import datetime

# 创建回测引擎
engine = BacktestEngine()

# 设置回测参数
start = datetime(2024, 1, 1)
end = datetime(2024, 12, 31)
setting = {
    "fast_window": 8,
    "slow_window": 15,
    "rsi_window": 10,
    "rsi_entry": 38
}

# 运行回测
result = engine.run_backtest(
    strategy_class=MediumFrequencyStrategy,
    setting=setting,
    symbol="BTCUSDT",
    start=start,
    end=end
)

# 获取回测结果统计
statistics = engine.calculate_statistics(result)
print(f"总收益率: {statistics['total_return']:.2f}%")
print(f"年化收益率: {statistics['annual_return']:.2f}%")
print(f"夏普比率: {statistics['sharpe_ratio']:.2f}")
print(f"最大回撤: {statistics['max_drawdown']:.2f}%")
```

### 3. 实盘交易

实盘交易模块提供了与币安交易所的集成，支持实时行情订阅和交易执行。

```bash
# 直接运行实盘交易脚本
python run_live_trading.py
```

实盘交易包含以下功能:
- 自动连接币安API
- 订阅实时行情
- 自动交易
- 自定义持仓管理
- 交易成本分析
- 依托VnTrader主界面进行监控

## 交易成本分析(TCA)

系统集成了简单的交易成本分析工具SimpleTCA，可以监控:
- 订单滑点（成交价格与订单价格的差异）
- 交易延迟（从下单到成交的时间）
- 百分比滑点（相对价格的滑点比例）

## 自定义持仓跟踪器

系统实现了自定义持仓跟踪器(CustomPositionTracker)，主要功能:
- 独立管理持仓信息，不依赖交易所API
- 追踪账户资金，支持资金冻结和解冻
- 持仓数据本地持久化，交易中断后可恢复
- 实时更新持仓和资金状态

## 注意事项

- 请确保已正确配置币安API密钥
- 建议先使用模拟盘测试策略
- 实盘交易前请充分回测验证策略有效性
- 系统默认使用币安的测试网环境
- 请勿在生产环境中使用示例中的API密钥

## 开发计划

- [ ] 增加更多交易策略
  - [ ] 趋势跟踪策略
  - [ ] 基于波动率的策略
- [ ] 优化回测性能
  - [ ] 支持多周期数据回测
- [ ] 添加风险管理模块
  - [ ] 最大持仓风险控制
  - [ ] 资金回撤保护
- [ ] 完善界面体验
  - [ ] 参数实时调整
  - [ ] 性能指标可视化
- [ ] 部署到服务器实现全天候自动交易

## 作者

MFE5210_Group4
