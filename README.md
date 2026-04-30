# game_zhanji

Legion of Valkyrie 联盟战报看板，用于把联盟成员截图和 Boss 伤害截图解析成可查看、可修正、可归档的运营数据面板。

## 功能概览

- 联盟成员 OCR：识别区服、昵称、职位、战斗力、最后在线时间等信息。
- Boss 周排行：识别 Boss 伤害排名，并按自然周统计累计伤害。
- 增量对比：同一周内不同时间节点的 Boss 数据会计算本次增量。
- OCR 复核：前端可修正成员和 Boss 识别结果，修正内容保存到 `data/corrections.json`。
- 截图上传：支持只上传联盟截图、只上传 Boss 截图，或两者同时上传。
- 本地重扫：重新解析 `records/` 下已有截图并生成最新状态文件。
- 周数据归档：已结束的 Boss 周数据会归档锁定，避免后续修正误改历史结果。

## 项目结构

```text
.
├── app.js                  # 前端交互逻辑
├── index.html              # 页面入口
├── styles.css              # 页面样式
├── server.py               # 本地 HTTP 服务和 API
├── requirements.txt        # Python 依赖
├── PRODUCT.md              # 产品说明
├── DESIGN.md               # 设计说明
├── data/
│   ├── state.json          # 当前聚合状态
│   ├── state.js            # 静态预览使用的初始状态
│   └── corrections.json    # 人工修正记录
├── records/
│   ├── members/            # 联盟成员截图记录
│   └── boss/               # Boss 截图记录
└── tools/
    └── lov_parser.py       # OCR 解析和状态构建脚本
```

## 环境要求

- Windows
- Python 3.10 或更高版本
- Windows OCR 组件
- Python 依赖：

```powershell
pip install -r requirements.txt
```

`requirements.txt` 当前包含：

- `winsdk`：调用 Windows OCR 能力
- `Pillow`：图片裁剪和处理

## 启动方式

在项目根目录运行：

```powershell
python server.py
```

启动后访问：

```text
http://127.0.0.1:8765/
```

服务启动时会先扫描并解析 `records/` 下已有截图，然后生成 `data/state.json` 和 `data/state.js`。

## 常用操作

### 上传截图

在页面右上区域上传联盟截图和/或 Boss 截图，点击上传识别。

- 联盟截图会保存到 `records/members/`
- Boss 截图会保存到 `records/boss/`
- 同一天同类型多次上传时，只保留当天最后一次记录
- 上传成功后会自动刷新页面状态

### 重新解析本地记录

页面中点击“重扫本地”，或在命令行运行：

```powershell
python tools/lov_parser.py --root .
```

该命令会重新读取 `records/` 目录，应用 `data/corrections.json` 中的修正，并重建状态文件。

### 修正 OCR 结果

在成员或 Boss 表格中打开复核/修正操作，保存后会写入：

```text
data/corrections.json
```

修正会在后续重扫和页面刷新中继续生效。

### 归档 Boss 周数据

页面中的归档操作会把已结束周的 Boss 数据锁定。归档后的 Boss 周数据不可再修改，用于保护历史周报。

## 数据说明

- `records/**/snapshot.json`：单次截图解析后的原始快照。
- `data/state.json`：前端和 API 使用的完整聚合状态。
- `data/state.js`：静态预览时使用的 `window.LOV_INITIAL_STATE`。
- `data/corrections.json`：人工修正、身份别名和复核记录。
- `data/archives/`：已归档的 Boss 周数据。

## API

本地服务提供以下接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/state` | 获取当前聚合状态 |
| `POST` | `/api/upload` | 上传联盟/Boss 截图并解析 |
| `POST` | `/api/reparse` | 重新解析本地截图 |
| `POST` | `/api/corrections` | 保存或删除 OCR 修正 |
| `POST` | `/api/archive-boss-weeks` | 返回当前归档状态 |

## 开发备注

- 前端是原生 HTML/CSS/JavaScript，不需要 Node.js 构建步骤。
- 直接打开 `index.html` 可以读取 `data/state.json` 做静态预览，但上传、修正和重扫需要通过 `python server.py` 启动本地服务。
- 项目内部分历史中文字符串存在编码异常，README 已按 UTF-8 重写；如继续维护 UI 文案，建议统一检查并保存为 UTF-8。
