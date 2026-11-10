# Windows 桌宠 2.0.0a10 候选

`overlay/` 保留 a10 的四个替换文件和两个新增文件。用 `base-sha256.json` 核对 a9 同路径文件的哈希。`null` 对应基线中没有该路径。将 `si-desktop-pet/` 复制为隔离目录，再将 `overlay/` 下文件按相同相对路径复制进去。候选继续使用 a9 的资源和移动端夹具。

候选暂停隐藏期间的运动，恢复后回到较稳定的待机状态，清除隐藏时未结束的拖拽，并修复 Qt 请求属性的整数枚举兼容性。先跑完整桌宠 unittest，再运行原生前台测试。

```powershell
# 在隔离的 a10 工程根目录执行。
python -m unittest discover -s tests -p 'test_*.py'
python tools/check_visibility_pause_native.py
```

原生测试使用真实桌面环境，需确认受控窗口获得前台。历史结果缺少有效阶段，见 [原生记录](../../docs/evidence/desktop-a10/native-visibility-pause.json)。发布前仍需取得有效前台全屏、恢复、拖拽和独立打包证据。
