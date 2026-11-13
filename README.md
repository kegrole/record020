# sched — 多站点限流请求调度器

只用 Python 标准库实现。多个采集站点各自有独立的配额、时间窗、优先级、
并发上限与失败退避策略；调度器基于**可注入的模拟时钟**做事件驱动调度，
单个站点被限流或进入冷却不会拖垮其他站点，总预算按配额公平分配。

## 运行

```bash
# demo（A/B/C 三个站点，预算 300）
python -m sched.cli --sites demo/sites.json --budget 300 --out out/report.json
# 也可以用包入口
python -m sched --sites demo/sites.json --budget 300 --out out/report.json
```

运行结束后 stdout 打印一行摘要（站点数、发出请求数、发生等待的站点数），
完整 JSON 报告写入 --out 指定的路径。

## 测试

```bash
cd tests && python -m unittest test_sched -v
# 或在仓库根目录
python -m unittest tests.test_sched -v
```

## 站点配置（JSON）

```json
{
  "global_max_concurrency": 4,
  "sites": [
    {
      "name": "A",
      "quota": 60,
      "period": "minute",
      "priority": 3,
      "max_concurrency": 2,
      "duration_ms": 40,
      "window": {"start_s": 0, "end_s": 600},
      "backoff": {"threshold": 3, "cooldown_s": 30}
    }
  ]
}
```

- quota / period：每个周期（second/minute/hour）允许的请求数，即令牌桶容量与补充速率。
- priority：整数 >= 1，越大优先级越高（同时作为公平调度权重）。
- max_concurrency：该站点同时在执行的请求数上限。
- duration_ms：单个请求的模拟耗时（占用并发槽的时长）。
- window：可选，半开区间 [start_s, end_s)，省略表示全天开放。
- backoff：连续失败 threshold 次后进入 cooldown_s 秒冷却。
- global_max_concurrency：全局并发上限，与每站点上限同时生效，超出排队。

## 配额与时间窗语义

- **令牌桶**：桶容量等于一个周期的配额，令牌按速率 quota/period 随模拟时间
  连续补充。桶空时请求**排队等待**（等待时长计入 waited_ms），不会因为
  限流而失败或丢弃。
- **时间窗**：窗口半开 [start_s, end_s)。窗口关闭时该站点本轮不参与调度，
  调度器直接把时钟快进到下一个事件（窗口开启、令牌就绪、冷却结束或请求
  完成），窗口开了再进。
- **预算分配**：总预算按各站点配额速率的比例切分（最大余数法取整，同余数
  时优先级高者优先）；预算不小于站点数时每个站点至少分到 1 次，保证不被
  饿死。若某站点剩余份额因窗口永久关闭而无法完成，会按比例再分配给仍可
  调度的站点。

## 公平性与优先级策略

- 每一轮在"窗口开放、未冷却、并发有槽位、持有令牌"的站点中，用**平滑加权
  轮询**（smooth weighted round robin）选择，权重即优先级：优先级高的站点
  被调度得更频繁，但就绪的低优先级站点每轮都能轮到，不会被饿死。
- 某站点被限流（桶空）或冷却时只是暂时离开候选集，其余站点照常调度，
  互不相干。
- 冷却期间令牌桶照常随时间补充，冷却结束后可以立即按配额突发。

## 失败退避

请求在完成时判定成功/失败（可注入 should_fail(site, index) 钩子，默认
全部成功）。连续失败达到 threshold 次即进入 cooldown_s 秒冷却；冷却期间
该站点被跳过（计入 skipped_for_cooldown），配额照常补充。

## 模拟时钟

sched.clock.SimClock 是注入式假时钟（毫秒），调度过程从不真实 sleep：
所有等待都通过 advance_to 跳到下一个事件时刻，因此运行完全确定——同样
的配置跑两次，报告逐字节一致，且一次 300 请求的模拟瞬间完成。

```python
from sched.clock import SimClock
from sched.config import load_config
from sched.dispatcher import Dispatcher
from sched.report import build_report

config = load_config("demo/sites.json")
clock = SimClock()
dispatcher = Dispatcher(config, clock, should_fail=lambda site, i: False)
dispatcher.run(300)
report = build_report(dispatcher, 300)
```

## 报告字段

顶层：budget、requests_sent、sim_duration_ms、global_peak_concurrency。
每个站点一段，固定 8 个字段：

| 字段 | 含义 |
| --- | --- |
| site | 站点名 |
| priority | 优先级（调度权重） |
| requests_sent | 实际发出的请求数 |
| quota_used | 消耗的令牌数（每请求 1 个） |
| waited_ms | 该站点请求累计排队等待的毫秒数 |
| skipped_for_cooldown | 因冷却被跳过的调度轮数 |
| peak_concurrency | 该站点并发峰值 |
| failures | 失败请求数 |

## 目录结构

- sched/config.py — 配置加载与校验
- sched/clock.py — 模拟时钟
- sched/ratelimit.py — 令牌桶
- sched/window.py — 时间窗
- sched/cooldown.py — 失败退避
- sched/dispatcher.py — 事件驱动调度器
- sched/report.py — JSON 报告
- sched/cli.py / sched/__main__.py — 命令行入口
- demo/sites.json — A/B/C 示例配置
- tests/test_sched.py — 覆盖全部判定口径的 unittest

