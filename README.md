# game_zhanji

Legion of Valkyrie 联盟战报看板，用于把联盟成员截图和 Boss 伤害截图解析成可查看、可修正、可归档的运营数据面板。

## 功能概览

- 联盟成员 OCR：识别区服、昵称、职位、战斗力、最后在线时间等信息。
- Boss 周排行：识别 Boss 伤害排名，并按自然周统计累计伤害。
- 每日增量：Boss 明细表按周展示周一到周日的每日伤害增量，并显示当前节点的本次增量。
- 历史归档统计：查看已归档 Boss 周的总伤害、成员数、第一名等历史统计。
- OCR 复核：前端可修正成员和 Boss 识别结果，修正内容保存到 `data/corrections.json`。
- 成员标注：支持手动补录联盟成员，并标注成员是否在群。
- 截图上传：支持只上传联盟截图、只上传 Boss 截图，或两者同时上传。
- 本地重扫：重新解析 `records/` 下已有截图并生成最新状态文件。

## 项目结构

```text
.
├── app.js                  # 前端交互逻辑
├── index.html              # 页面入口
├── styles.css              # 页面样式
├── server.py               # 本地 HTTP 服务和 API
├── requirements.txt        # Python 依赖
├── data/                   # 聚合状态、修正和归档数据
├── records/                # 原始截图记录
└── tools/lov_parser.py     # OCR 解析和状态构建脚本
```

## 环境要求

- Windows
- Python 3.10 或更高版本
- Windows OCR 组件

安装依赖：

```powershell
pip install -r requirements.txt
```

## 启动

```powershell
python server.py
```

访问：

```text
http://127.0.0.1:8765/
```

服务启动时会扫描 `records/` 下已有截图，并生成 `data/state.json` 和 `data/state.js`。

## 常用操作

### 上传截图

在页面上传联盟截图和/或 Boss 截图，点击“上传并识别”。

- 联盟截图保存到 `records/members/`
- Boss 截图保存到 `records/boss/`
- 同一天同类型多次上传时，只保留当天最后一次记录

### 重新解析本地记录

页面点击“重扫本地”，或运行：

```powershell
python tools/lov_parser.py --root .
```

### 修正 OCR 结果

在成员或 Boss 表格中点击“核对”，保存后写入：

```text
data/corrections.json
```

### 归档 Boss 周数据

页面点击“归档已结束周”，已结束的 Boss 周数据会归档并锁定。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/state` | 获取当前聚合状态 |
| `POST` | `/api/upload` | 上传联盟/Boss 截图并解析 |
| `POST` | `/api/reparse` | 重新解析本地截图 |
| `POST` | `/api/corrections` | 保存或删除 OCR 修正 |
| `POST` | `/api/archive-boss-weeks` | 返回当前归档状态 |

## 备注

前端是原生 HTML/CSS/JavaScript，不需要 Node.js 构建步骤。上传、修正和重扫需要通过 `python server.py` 启动本地服务。
