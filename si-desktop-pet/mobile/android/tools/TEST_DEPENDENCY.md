# JVM 协议测试依赖

应用 APK 使用 Android 系统的 org.json，不包含此 JAR。仅在电脑上测试生产 Java 协议代码时，需要 JSON-java 的 JVM 实现。

版本：org.json:json:20250517。

下载：https://repo.maven.apache.org/maven2/org/json/json/20250517/json-20250517.jar

放到本目录，文件名为 json-jvm-tests.jar。SHA-256：`3ea61b2a06e31edf1c91134fe9106b0ebb16628be169f3db75bc7a2b06b45796`。

对应项目：https://github.com/stleary/JSON-java 。源码包不附带 JAR，APK 构建不依赖此测试文件。测试入口为 ../tests/run_protocol_checks.py，可用 SI_ANDROID_TEST_JDK 指定 JDK 17 根目录；Python 侧需安装 cryptography 及工程协议代码。
