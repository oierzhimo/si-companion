# 署名与第三方组件

## 行为参考

- Ark-Pets — isHarryh 与贡献者，https://github.com/isHarryh/Ark-Pets ，GPL-3.0。
- 本项目参考公开 README 中的桌宠行为说明，未复制 Ark-Pets 的 Java 实现、启动器、Spine 模型库或下载服务。保留参考说明与 GPL 文本供查阅。

## 运行依赖

- PySide6-Essentials 6.12.0 / Shiboken6 6.12.0 — Qt for Python Team，https://doc.qt.io/qtforpython/ 。包元数据许可表达式：`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`。此项目以开源许可使用并动态分发其库，见 `licenses/LGPL-3.0.txt`、`licenses/GPL-3.0.txt`。对应源代码：https://code.qt.io/cgit/pyside/pyside-setup.git/ ，Qt 基础库：https://code.qt.io/cgit/qt/qtbase.git/ 。可使用兼容 ABI 的开源构建替换 `_internal/` 中对应 Qt/PySide 动态库，或使用随附源码重新构建本项目。
- Python 3.12 — Python Software Foundation，PSF 许可，见 `licenses/Python-PSF.txt`。
- cryptography 50.0.2 — Python Cryptographic Authority，Apache-2.0 或 BSD，https://cryptography.io/ 。用于可选局域网协同的本机证书生成，许可证见 `licenses/cryptography/`。
- cffi 2.1.1 与 pycparser 3.12 — 加密组件的依赖，许可证随实际安装包保存在 `licenses/cffi/` 和 `licenses/pycparser/`。
- PyInstaller 6.22.3 — 构建工具，GPL-2.0-or-later 与启动器分发例外，https://pyinstaller.org/ 。本项目保留其实际安装包携带的许可证文本。
- Pillow 12.3.0 — 素材构建工具，HPND 等许可，https://python-pillow.github.io/ 。用于开发时提取帧，不作为应用入口运行依赖。

## 角色与素材

角色祀及《明日方舟：终末地》相关权利归原权利人所有。本项目所附桌宠素材由图像生成工具依据用户指定角色参考生成，属于非官方同人创作。参考资料链接保存在 `art-source/SOURCES.md`；用户原始游戏截图和账号标识不随发布包分发。

代码 GPL 许可与上述第三方许可不授予原角色的商标、肖像或其他原作权利。
