# CLAUDE.md —— trademap 一页纸

交易辅助看盘页（本机 127.0.0.1:8765）。**只看，不下单**：这里没有任何交易所 key，也不许加。

- 信号在 Python 里算（`trademap/td9.py`、`trademap/sweepmarks.py`），页面（`chart/src/`）只画不判；
  一个信号的逻辑如果页面和 Python 各算一份，就一定会有一天不一致。
- 扫底回收检测器在 farseer 仓 `src/farseer/research/sweep/`，本仓只调用不复制；它的预注册在 farseer。
- `data/` 是操作者手画的线，不入库、不许测试触碰（测试用 `tmp_path`）。
- `chart/dist/` 不入库，改了前端要 `npm run build`。
- 周期取值见 `trademap/chartbars.py` TIMEFRAMES 与 `chart/src/timeframes.js`，两边要一致。
- 给操作者的输出用中文，先给结论；时间 AEST/AEDT。
