# app · 工程本体

这个目录是可以直接跑的全部内容。

```
index.html        页面本体，HTML/CSS/JS 全内联，双击即开
update_data.py    数据管线，纯标准库，20 只 ETF 约 25 秒
run_daily.bat     Windows 双击跑更新
data/
  snapshot.js     页面读这个（window.SNAPSHOT = {...}）
  snapshot.json   同一份数据的 JSON，方便自己用 pandas 分析
deploy/
  nginx-etf-watch.conf   静态站配置，含 noindex
  etf-watch.cron         定时任务（09:45 / 15:35 各一次）
  run_once.sh            跑批包装，带日志与告警钩子
  backup.sh              把 data/ 打包传对象存储
```

```bash
python update_data.py --test   # 只跑前 3 只，验证接口
python update_data.py          # 全量，约 25 秒
```

然后双击 `index.html`。

- 使用、部署、常见问题 → [`../docs/01_使用与部署.md`](../docs/01_使用与部署.md)
- 数据源字段、算法推导、坑 → [`../docs/02_指标口径与实现.md`](../docs/02_指标口径与实现.md)

`data/` 是脚本生成的，删掉能重建，但份额历史会一起丢，注意备份。
