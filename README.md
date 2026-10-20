# 医药数据分析问数 Agent · 离线演示

这是按企业医药数据分析平台的公开能力描述复现的**功能与架构 demo**，**不是企业原始项目源码**。业务数据、知识库内容均为合成示例，原项目的业务表规模、评测集和效果指标不能作为本 demo 的成绩。

当前实现：FastAPI + SQLite + 原生 JavaScript + ECharts，规则问数和原文抽取，不需要 API Key，也不依赖外部数据库或模型服务。首轮自评与修复前的缺口清单见 [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md)。

## 启动

推荐 Python **3.12**（本轮验证 3.12.10）。Clone 后在仓库根目录运行：

```powershell
git clone https://github.com/Dithob/medical-data-agent.git
cd medical-data-agent
.\run.ps1
# 自定义端口
.\run.ps1 --port 8011
# 只检查环境
.\run.ps1 --check
```

如 PowerShell 的脚本策略不允许运行 `.ps1`，可直接使用 Python 入口，无需修改系统策略：

```powershell
py -3.12 run.py
```

macOS/Linux 的入口为 `bash run.sh`，或 `python3 run.py`。入口不含个人电脑路径；本轮未在这两个系统上实测。

浏览器访问 [本机演示](http://127.0.0.1:8000/)。按 Ctrl+C 停止服务。

启动器自动在仓库根目录创建 `.venv/` 并按 `requirements.lock` 安装依赖，**首次安装需要网络**。安装后用 `--no-install` 可禁止自动安装并离线启动；图表、Markdown、语法高亮和 HTML 净化依赖都在 `static/vendor/`。

运行中的任务登记保存在单进程内存，启动器固定一个 worker。不要通过 `--workers` 启动多进程来共享任务状态。

## 页面与支持范围

| 页面 | 功能 |
| --- | --- |
| `/index.html` | 实际离线架构、与原项目的差异 |
| `/chat.html` | 销售/采购/库存问数，SQL 与 trace、分页表格、单/多系列图表、当前结果 Excel 导出 |
| `/dashboard.html` | 12 列布局，拖拽/缩放、碰撞下移、KPI/表格/图表、刷新策略与后端保存，网络故障时本地草稿兜底 |
| `/rag.html` | 条款检索和原文抽取、引用版本预览、打字机、停止、重生成和历史消息 |
| `/admin.html` | 真实数据源/引擎状态、文档上传与版本、含 SQL 的请求明细 |

问数示例：

- `2025年第一季度销售额`、`2025年上半年销售额`、`近12个月销售额趋势`
- `阿莫西林销售额`、`华东区域销售额`、`上海供应商采购额`
- `销量前10的药品`、`当前库存金额`、`近效期库存记录数`
- `各月份各区域销售额趋势`（两个维度生成多系列图表）

当前每次查询一个指标、最多两个分组维度。季度支持中文/数字和 Q1～Q4；实体支持内置药品名称前缀、城市、区域、品类、门店类型及处方标识。问数规则是有限集合，不能覆盖任意自然语言。同比、环比、预测、周转、客单价和跨事实表联合指标会提示不支持；不提供跨问题语义记忆。

`销售额/销售数量` 的指标名为“平均销售单价”。示例库没有订单、客户或真实批次 ID，不以这些字段计算客单价或实际批次数。

## 数据与日期口径

默认演示参考日为 **2026-08-31**，页面顶部始终显示，避免随着日历变化让预置问题失效。日期可显式配置；它不是实时业务日期。

| 事实数据 | 行数 | 覆盖区间 |
| --- | ---: | --- |
| 销售 | 19,696 | 2025-01-01～2026-08-31 |
| 采购 | 1,758 | 2025-01-01～2026-08-30 |
| 库存月度快照 | 9,600 | 2025-01-28～2026-08-28 |

种子随机数固定，合计 31,054 行事实记录。库存汇总默认取截止日前最新可用快照，月度趋势逐快照统计；近效期以演示参考日开始的 180 天为窗口，排除已过期记录。大屏近效期指标与问数共用参考日规则。

查询最多返回 500 行，并返回 `returnedRows/hasMore/truncated`。Excel 导出的是这次已经返回的结果，超过 500 行会明确提示；需要更完整数据时请缩小时间或条件范围。指定 TopN 的限制是用户查询范围，不当作系统截断。

## 配置

| 变量/参数 | 默认值 | 用途 |
| --- | --- | --- |
| `MEDICAL_RAG_DATA_DIR` | 仓库根目录下 `data/` | 数据库、旧文档迁移目录；建议使用绝对路径 |
| `MEDICAL_RAG_AS_OF_DATE` | `2026-08-31` | ISO 格式演示参考日 |
| `MEDICAL_RAG_VENV` | 仓库根目录下 `.venv/` | 隔离 Python 环境路径，建议绝对路径 |
| `PYTHON` | Windows 用 `py -3.12`，Shell 用 `python3` | 指定入口解释器 |
| `PORT` / `--port` | `8000` | 本机端口，命令行优先 |
| `--no-install` | 关闭 | 缺依赖时直接提示，不联网安装 |

示例：

```powershell
$env:MEDICAL_RAG_DATA_DIR = (Join-Path $PWD 'data-experiment')
$env:MEDICAL_RAG_AS_OF_DATE = '2026-10-01'
.\run.ps1 --no-install
```

参考日改为 2026-10-01 后，“上个月销售额”会准确提示没有数据，而不会显示 None。重开普通终端即可恢复未设置这些变量的默认环境。

## 文档、消息与任务

- 文档只接收 UTF-8 `.txt/.md`，最大 2 MiB，拒绝含路径的文件名。不支持 PDF。
- 上传内容存入 SQLite 文档版本表，**不按客户端文件名写磁盘**。文档 UUID 与内容哈希持久化；同名且相同内容去重，同名不同内容创建不可变版本。
- 在线检索只使用每份文档的最新可用版本；旧回答的 `docId` 指向不可变版本，预览不会随着其他文件上传而改变。
- 索引片段上限 128 **字符**，检索到的片段回到对应条款作为证据。每段证据最多 2,000 字符；没有真实 Embedding 或重排模型。
- 重新回答保留原问题与旧回答版本，只在新回答成功后切换当前版本，不重复新增用户消息。
- `taskId` 用于取消与查询 `/api/chat/tasks/{taskId}`。取消通过协作检查及 SQLite progress handler 生效；完成事务与取消有互斥边界。已完成任务不会被改写成“已取消”。终态保留约 10 分钟。
- 回答已返回后，RAG 按钮显示“停止显示”，只停止前端打字机。数据库仍保存完整回答，刷新历史可恢复。

## 执行边界

业务 SQL 使用 URI `mode=ro`、SQLite authorizer 的业务表/函数白名单、2 秒默认执行时限及 500 行返回上限。SQL 生成参数化执行；自纠错只允许已知字段别名，最多重试一次。

用户消息用纯文本渲染，回答 Markdown 由本地 DOMPurify 净化。Excel 文本单元格处理公式前缀。外部数据库/模型配置未开放，不会用 SQLite 假装外部连接成功。

服务默认仅监听 `127.0.0.1`，没有多用户认证与生产部署能力。统计中的“执行完成”只表示 SQL 执行成功，语义质量需看独立评测；无数据、无证据、拒绝、失败和取消分别记录。

## 迁移

已有 `platform.db` 会增量添加消息关联/版本字段及独立文档版本表，不删除会话和大屏。首次迁移按旧顺序导入 `data/uploads/` 下合法且符合大小/编码要求的 TXT/Markdown；不读取越界路径或符号链接。

仅匹配旧版内置近效期 SQL 的卡片会自动更正；自定义 SQL 不被重写。旧实现已经产生过错误关联的历史引用无法从缺失信息中自动复原。重要的旧数据建议先保留副本再升级。

## 回归测试

在 demo 目录运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_api.py -v
```

API 测试依赖已纳入锁定环境，不会连接外部服务；测试使用临时数据库。测试包括独立 SQL 基准、RAG 条款关键事实、只读/超时、上传边界、引用版本、消息重生成、取消与旧数据迁移。

浏览器测试额外安装开发依赖及 Chromium：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tests/browser_regression.py --python .venv/Scripts/python.exe
```

也可通过 `--browser <Chromium绝对路径>` 使用已安装的浏览器。测试自动启动并停止隔离服务，输出至忽略目录 `test-results/`，阻止外部网络请求。macOS/Linux 将解释器路径替换为 `.venv/bin/python`。

## 代码结构

```text
server/
  app.py          路由、参数约束、响应与持久化
  text2sql.py     QueryIntent、时间/实体解析、SQL 生成与纠错
  metrics.py      指标公式与共享库存规则
  executor.py     只读执行、取消、超时、行数上限
  tasks.py        单进程任务生命周期与发布边界
  documents.py    稳定文档与不可变版本
  rag.py          原子索引更新、词面检索、条款抽取
  db.py           示例库和平台库、增量迁移
static/js/        页面、请求作用域与图表实例管理
static/vendor/    离线前端依赖（含 DOMPurify 许可证）
tests/           API 基准与浏览器回归
```

推荐讲解流程见 [DEMO_SCRIPT.md](DEMO_SCRIPT.md)。真实模型接入属于方案 E 的可选下一阶段，当前没有接入或宣称具备该能力。
