# LarkExcelCalendar

查询 Aaron、Alvin、Thomas 的日历忙闲时段，写入 Lark 电子表格供外部查看。

## 目标表格

https://hff6qb5z7p3.sg.larksuite.com/wiki/XVvEwZZmtigQPGk4BR6lV8DAgGb

## 运行

```bash
# 本地运行（需要 .env 文件）
python3 sync_to_sheet.py

# 仅查看数据不写入
python3 sync_to_sheet.py --dry-run

# 详细日志
python3 sync_to_sheet.py --verbose
```

## 自动运行

GitHub Actions 每 4 小时自动执行一次。

## 依赖

- Python 3.11+
- [lark-cli](https://www.npmjs.com/package/@larksuite/cli) (`npm install -g @larksuite/cli`)
