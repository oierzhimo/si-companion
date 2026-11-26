/* =========================================================
   小祀 · Si Companion Showcase v2.1.2 — Interactive Logic, Live Sync & i18n
   ========================================================= */

const CONFIG = {
  feedbackEmail: "2477160494@qq.com",
  formSubmitEndpoint: "https://formsubmit.co/ajax/2477160494@qq.com",
  githubRepo: "https://github.com/oierzhimo/si-companion",
  windowsDownloadUrl: "https://github.com/oierzhimo/si-companion/releases/download/v2.1.2/SiDesktopPet-2.1.2-Windows-x64.zip",
  sha256: "f4b2246badec72a1aff137c3029d35a2a66107b3f24d44634f4e1caccecb656c"
};

const MOODS = {
  happy: {
    img: "assets/happy.png",
    state: "waving",
    frames: 4,
    fps: 6.5,
    loop: 2, // Wave warmly twice then settle into natural breathing/blinking idle!
    zh: "（祀执笔轻轻落墨）哼，管理员，累了的话就先歇一小会儿吧。",
    en: "Administrator, I'm right here. If you're tired, take a short break."
  },
  neutral: {
    img: "assets/neutral.png",
    state: "idle",
    frames: 6,
    fps: 6,
    loop: "idle_gaze",
    zh: "我就安静待在屏幕角落，视线轻轻跟着你，打游戏时绝不出声吵你。",
    en: "I'll stay quietly in the corner, gently follow your cursor, and mute automatically during games."
  },
  sitting: {
    img: "assets/neutral.png",
    state: "sitting",
    frames: 4,
    fps: 5.5,
    loop: "hold_last",
    zh: "坐一会儿。你继续忙你的，我就在桌边看着。",
    en: "Sitting down for a bit. Keep going—I'm watching quietly from the edge."
  },
  blush: {
    img: "assets/blush.png",
    state: "running",
    frames: 6,
    fps: 6.5,
    loop: "hold_last",
    zh: "咳咳……给我的点心？那本姑娘就勉为其难收下啦。",
    en: "Ahem... A snack for me? Well then, I'll gladly accept it."
  },
  thinking: {
    img: "assets/thinking.png",
    state: "review",
    frames: 6,
    fps: 6,
    loop: "hold_last",
    zh: "（指尖轻托下颌端详）卡住的时候不用急，换个角度落笔试试？",
    en: "No rush if you're stuck—try looking at it from a fresh angle."
  },
  walk: {
    img: "assets/happy.png",
    state: "walk-right",
    frames: 8,
    fps: 8,
    loop: "infinite",
    zh: "别急，我沿着窗口边缘随处散散步。",
    en: "No hurry—I'm just taking a gentle stroll along your window ledges."
  },
  sleepy: {
    img: "assets/sleepy.png",
    state: "sleeping",
    frames: 4,
    fps: 5,
    loop: "hold_last",
    zh: "哈啊……稍微打个盹，有事点我一下就好。",
    en: "Yawn... Just resting my eyes for a bit. Click me anytime you need me."
  }
};

const I18N = {
  zh: {
    pageTitle: "小祀 · Si Companion — 桌面陪伴伙伴 (v2.1.2)",
    langButton: "English",
    "brand.name": "小祀 · Si Companion",
    "nav.intro": "项目简介",
    "nav.sync": "实时同步",
    "nav.download": "下载",
    "nav.guide": "新手教程",
    "nav.interactions": "互动玩法",
    "nav.faq": "常见问题",
    "nav.feedback": "意见反馈",

    "hero.badge": "Windows 10/11 绿色便携版 · v2.1.2 全新发布",
    "hero.title": "小祀 · 陪在你桌面上的灵动伙伴",
    "hero.subtitle":
      "无需编程基础、无需安装 Python，解压双击就能把小祀请到桌面。v2.1.2 升级 3D 防穿模水墨金玉视觉、Soul Engine V1.2 数字人格，并在接入耳机且未开 Apex / 彩六等本地游戏时自动同步输出语音、拟声词与动作音效。",
    "hero.ctaDownload": "立即下载 Windows 版 (v2.1.2)",
    "hero.ctaSync": "实时聊天与陪伴同步",
    "hero.pill1": "✓ 免安装绿色解压即用",
    "hero.pill2": "✓ 耳机感知 + 游戏自动静音 TTS",
    "hero.pill3": "✓ API Key 仅留内存不写盘",
    "hero.pill4": "✓ GPL-3.0 开源免费",
    "hero.previewTitle": "3D 实时灵动舞台 · 移动鼠标跟随视线",
    "hero.modeFull": "半身特写",
    "hero.modeBust": "全身动态",

    "mood.happy": "摸摸头",
    "mood.neutral": "视线跟随",
    "mood.sitting": "坐下小憩",
    "mood.blush": "送点心",
    "mood.thinking": "聊画/端详",
    "mood.walk": "散步",
    "mood.sleepy": "打盹",

    "intro.kicker": "项目简介",
    "intro.title": "什么是 si_accompany（小祀桌宠 v2.1.2）？",
    "intro.desc":
      "小祀（Si Companion / si_accompany）是一个以《明日方舟：终末地》角色「祀」为形象的非官方开源同人桌面伙伴项目。主打低打扰、离线可用、界面清爽，并提供 Soul Engine V1.2 人格对话、智能语音音效与跨端数据同步。",

    "feat1.title": "3D 精修动作与防穿模升级",
    "feat1.desc":
      "v2.1.2 修复蹲坐裙摆穿模并重绘五种高清神态头像，支持自主散步、窗口顶边落脚、拖拽重力与十六方向眼神跟随。",
    "feat2.title": "智能耳机语音 + 拟声词与动作声",
    "feat2.desc":
      "接入耳机且未打开 Apex、彩虹六号等本地游戏时默认同时输出语音与文本；支持「哼、咳咳、噗嗤」拟声词与执笔落墨、推茶盏、盖印章等动作音效。",
    "feat3.title": "Soul Engine V1.2 数字人格与识图",
    "feat3.desc":
      "搭载专属微调人格模型，支持本机 Ollama 离线运行、DeepSeek 官方 API 与学校/第三方兼容接口，多模态窗口可直接拖入图片交流。",
    "feat4.title": "水墨金玉深浅主题自适应",
    "feat4.desc":
      "面板、带头像气泡的聊天界面、可折叠连接抽屉与右键菜单自动跟随 Windows 浅色或深色模式切换高对比度配色。",
    "feat5.title": "数据本地保存，隐私安心",
    "feat5.desc":
      "亲密度、设置和对话记录全部存放在程序同目录的 data/ 文件夹中；API Key 仅留在运行内存，绝不写入硬盘。",
    "feat6.title": "跨设备与广域网聊天数据同步",
    "feat6.desc":
      "支持 sync-v1 事件账本、局域网 TLS 配对、Android 预览包及网页端实时陪伴状态与对话历史双向同步。",

    "sync.kicker": "实时连接",
    "sync.title": "小祀陪伴状态与聊天数据同步",
    "sync.desc":
      "实时同步本机桌宠的亲密度、心情、相伴手记与最近对话记录。通过广域网实时节点访问时，可直接在线和小祀互动或发送消息同步回桌面端。",
    "sync.connecting": "正在读取小祀同步状态…",
    "sync.liveOnline": "🟢 实时在线 · 已连接本机小祀同步账本与 Soul Engine V1.2",
    "sync.staticSnapshot": "🟡 静态快照模式（GitHub Pages 镜像）· 显示已同步的最新陪伴记录",
    "sync.patBtn": "👋 远程摸摸头",
    "sync.snackBtn": "🍵 送一份点心",
    "sync.refreshBtn": "↻ 立即刷新",
    "sync.statAffinity": "亲密度",
    "sync.statMood": "当前心情",
    "sync.statPats": "摸头次数",
    "sync.statSnacks": "投喂点心",
    "sync.statFocus": "专注完成",
    "sync.statMinutes": "相伴时长",
    "sync.voiceDefault": "智能语音策略：接入耳机且未运行本地游戏时默认语音+文本同步输出",
    "sync.chatTitle": "💬 同步对话记录（与桌面端互通）",
    "sync.chatPlaceholder": "在广域网实时节点直接和小祀说句话，会同步写入桌面端…",
    "sync.chatSend": "发送",
    "sync.chatHint": "提示：实时隧道在线时可直接与本机小祀模型对话并双向同步；GitHub Pages 静态镜像展示最新同步快照。",
    "sync.journalTitle": "📔 相伴手记（最新动态）",
    "sync.emptyChat": "暂无已同步的对话记录，发一句话和小祀打个招呼吧。",
    "sync.emptyJournal": "第一段相伴，从现在开始。",

    "dl.kicker": "获取软件",
    "dl.title": "下载小祀（si_accompany v2.1.2）",
    "dl.desc": "新手直接下载下方的 <strong>Windows 绿色便携版</strong> 即可，无需配置任何开发环境。",
    "dl.recommended": "新手推荐",
    "dl.winTitle": "祀桌宠 · Windows 绿色便携版",
    "dl.winDesc":
      "解压即用，内置完整运行环境、3D 防穿模精修动作帧组、立绘头像与中文指南。支持耳机/游戏感知智能语音（含拟声词与动作音效）、自适应深浅主题及多模型对话。",
    "dl.fileLabel": "文件名：",
    "dl.sizeLabel": "大小：",
    "dl.winBtn": "下载 Windows 版 (v2.1.2)",
    "dl.repoBtn": "访问 GitHub 仓库",
    "dl.copy": "复制",
    "dl.copied": "已复制 ✓",
    "dl.androidTitle": "Android 预览版 (v0.1.3-preview)",
    "dl.androidDesc":
      "支持手机端基础陪伴、悬浮展示与局域网 TLS 配对同步（需配合 Windows 端协同预览模式使用，不适用于原生鸿蒙 5）。",
    "dl.androidLink": "在 Releases 查看 Android 包 →",
    "dl.sourceTitle": "HarmonyOS 5 工程预览 & 完整源码",
    "dl.sourceDesc":
      "面向开发者的完整仓库，包含桌面端源码、Soul Engine 数据构建、QQ 多模态原型、HarmonyOS 5 工程预览与打包脚本。",
    "dl.sourceLink": "浏览 GitHub 源码 →",

    "guide.kicker": "零基础教程",
    "guide.title": "新手 4 步上手指南",
    "guide.desc":
      "第一次用绿色压缩包软件？跟着下面 4 个步骤一步一步操作，2 分钟就能让小祀出现在你的桌面上。",

    "step1.title": "下载压缩包，并「完整解压」到普通文件夹",
    "step1.desc":
      "点击上方的下载按钮获取 <code>SiDesktopPet-2.1.2-Windows-x64.zip</code>。下载完成后，<strong>右键点击该压缩包</strong> → 选择「全部解压缩」（或用 7-Zip / WinRAR 解压），解压到一个好找的位置，例如 <code>D:\\SiDesktopPet</code>。",
    "step1.warnTitle": "⚠️ 新手最容易踩的坑：",
    "step1.warnBody":
      "请务必<strong>完整解压整个压缩包</strong>后再运行！不要在压缩包预览窗口里直接双击 EXE，也不要只把 EXE 单独拖到桌面，否则程序会找不到素材和运行库。",

    "step2.title": "双击启动 <code>Si-DesktopPet.exe</code>",
    "step2.desc":
      "打开解压后的文件夹，找到带小祀图标的 <code>Si-DesktopPet.exe</code> 并双击运行。首次打开会同时显示「陪伴面板」和桌面上的小祀，离线摸头、投喂和专注计时立即可用！",
    "step2.tipTitle": "💡 如果弹出蓝色的「Windows 已保护你的电脑」提示：",
    "step2.tipBody":
      "这是 Windows 对未购买商业签名的个人开源软件的常规提示。点击弹窗里的 <strong>「更多信息」</strong> → 再点击 <strong>「仍要运行」</strong> 即可正常启动。",

    "step3.title": "（可选）配置 AI「自由聊聊」、多模态看图与智能语音",
    "step3.desc":
      "如果想让小祀接入大模型和你自由聊天，在陪伴面板右侧点击「自由聊聊」或「多模态聊聊」，按你的条件三选一；若本机装有 VoiceStudio，插上耳机且未开游戏时会自动同步发声：",

    "tab.deepseek": "方式 A：DeepSeek 官方（最省心）",
    "tab.custom": "方式 B：学校 / 第三方 API",
    "tab.local": "方式 C：本机 Ollama 离线模型",
    "tab.deepseek1":
      '1. 前往 <a href="https://platform.deepseek.com" target="_blank" rel="noopener noreferrer">platform.deepseek.com</a> 注册并创建一个 API Key（以 <code>sk-</code> 开头）。',
    "tab.deepseek2":
      "2. 在小祀聊天窗口里，「连接方式」选择 <strong>DeepSeek 官方 API</strong>，地址会自动填好，默认模型 <code>deepseek-chat</code>。",
    "tab.deepseek3":
      "3. 粘贴你的 API Key 即可开始纯文字聊天（Key 仅留在当前内存，关闭软件不写盘）。",
    "tab.custom1": "1. 「连接方式」选择 <strong>自定义模型接口（学校 API / 第三方）</strong>。",
    "tab.custom2":
      "2. 填入学校或平台提供的 OpenAI 兼容 Base URL（如 <code>https://api.school.edu.cn/v1</code>）并粘贴 API Key。",
    "tab.custom3":
      "3. 点击右侧 <strong>「获取模型」</strong> 选择可用模型；若所选模型支持视觉，在「多模态聊聊」里拖入图片就能一起聊。",
    "tab.local1":
      "1. 先在电脑上启动 <strong>Ollama</strong>（默认端口 <code>11434</code>）或 <strong>LM Studio</strong>（默认端口 <code>1234</code>）。",
    "tab.local2": "2. 「连接方式」选择 <strong>本机模型（Ollama / 本地端口）</strong>，默认适配 <code>si-qwen2.5-3b-lang-lora:latest</code>。",
    "tab.local3": "3. 点击「获取模型」选择本机已下载模型，完全离线运行不消耗外部流量。",

    "step4.title": "日常小贴士：找回小祀、退出与备份记录",
    "step4.tip1":
      "<strong>找不到桌宠 / 开了鼠标穿透？</strong> 按快捷键 <code>Ctrl + Alt + S</code>，或右键电脑右下角任务栏托盘的小祀图标，点击 <strong>「找回祀」</strong>。",
    "step4.tip2":
      "<strong>怎么彻底退出？</strong> 关闭陪伴面板后小祀会继续留在桌面；右键小祀或托盘图标选择「退出桌宠」即可关闭。",
    "step4.tip3":
      "<strong>换电脑 / 升级新版本？</strong> 先完全退出旧版，把文件夹里的 <code>data/</code> 复制到新版目录，亲密度与聊天记录就会完整保留。",

    "inter.kicker": "操作速查",
    "inter.title": "怎么和小祀相处？",
    "inter.desc": "记住下面这些简单的鼠标和快捷键操作，就能玩转全部功能。",
    "inter.k1": "单击 / 摸摸头",
    "inter.v1": "戳一戳、招手并弹出角色短句，增加亲密度。",
    "inter.k2": "按住拖拽并松开",
    "inter.v2": "把小祀放到屏幕任意位置、扩展屏或普通窗口顶边，松手后自然下落。",
    "inter.k3": "双击桌宠",
    "inter.v3": "在当前落脚面上轻轻跳起。",
    "inter.k4": "右键桌宠",
    "inter.v4": "切换散步、坐下、打盹、招手等动作，打开面板或退出程序。",
    "inter.k5": "耳机智能语音",
    "inter.v5": "插上耳机自动语音+文本双输出（含拟声词与动作声），开 Apex/彩六自动静音避让。",
    "inter.v6": "一键找回桌宠到主屏幕、取消鼠标穿透并恢复显示。",

    "faq.kicker": "答疑解惑",
    "faq.title": "新手常见问题 (FAQ)",
    "faq.q1": "我不懂编程、电脑没装 Python，可以直接用吗？",
    "faq.a1":
      "完全可以！Windows 便携版已经把所有运行环境都打包好了，下载 ZIP 后完整解压，双击 Si-DesktopPet.exe 就能直接运行，不需要安装 Python 或任何命令行工具。",
    "faq.q2": "打 Apex、彩虹六号等游戏时小祀会突然出声干扰听脚步吗？",
    "faq.a2":
      "不会！v2.1.2 内置实时音频端点与本地游戏进程检测：仅在接入耳机且未运行 Apex、彩虹六号、CS2、瓦罗兰特、永劫无间等游戏时自动发声；一旦打开游戏或拔下耳机，会立即自动静音并切换为纯文本显示。",
    "faq.q3": "下载打开时 Windows 提示「已保护你的电脑」，是病毒吗？",
    "faq.a3":
      "不是病毒。由于个人开源项目没有购买昂贵的商业代码签名证书，Windows SmartScreen 会对新打包的 EXE 弹出提醒。点击「更多信息」→「仍要运行」即可，你也可以核对上方提供的 SHA-256 校验值或直接查看 GitHub 开源代码。",
    "faq.q4": "我的 API Key 和聊天记录安全吗？",
    "faq.a4":
      "所有本地对话记录与设置仅保存在你电脑的 data/ 目录中；API Key 只保留在程序运行内存里，绝不会写入本地文件或上传给第三方。",
    "faq.q5": "开启「鼠标穿透」后点不到小祀了怎么办？",
    "faq.a5":
      "按下键盘快捷键 Ctrl + Alt + S，或者在电脑右下角任务栏系统托盘找到小祀图标，右键选择「找回祀」即可立刻恢复可点击状态。",

    "fb.kicker": "联系与改进",
    "fb.title": "意见反馈",
    "fb.desc":
      "遇到下载问题、运行报错，或对小祀有新的功能想法？欢迎直接填写下方表单，你的反馈将直接发送至作者邮箱 <strong>2477160494@qq.com</strong>。",
    "fb.typeLabel": "反馈类型",
    "fb.typeSuggestion": "功能建议 / Feature Idea",
    "fb.typeBug": "问题 / Bug 反馈",
    "fb.typeHelp": "下载或使用求助",
    "fb.typeOther": "其他留言",
    "fb.platformLabel": "使用版本 / 平台",
    "fb.platformNone": "还未下载 / 咨询",
    "fb.nameLabel": "你的称呼（选填）",
    "fb.namePlaceholder": "例如：管理员 / 小明",
    "fb.contactLabel": "你的邮箱或 QQ（选填，方便回复你）",
    "fb.contactPlaceholder": "例如：yourname@qq.com",
    "fb.msgLabel": "反馈内容",
    "fb.msgPlaceholder": "请描述你遇到的问题（如系统版本、错误提示）或想给小祀加的新功能...",
    "fb.submitBtn": "直接发送至作者邮箱",
    "fb.mailtoBtn": "用本机邮箱客户端发送",
    "fb.directLabel": "也可以直接发邮件至：",
    "fb.sending": "正在发送反馈至 2477160494@qq.com，请稍候...",
    "fb.emptyMsg": "请先填写反馈内容再发送。",
    "fb.success": "✅ 反馈已成功发送至 2477160494@qq.com！感谢你的建议与支持。",
    "fb.fallback":
      "⚠️ 当前网络或本地预览环境无法直连在线发送服务。请点击右侧「用本机邮箱客户端发送」按钮，或直接复制邮箱 2477160494@qq.com 联系作者。",

    "footer.disclaimer":
      "「祀」、《明日方舟：终末地》及相关角色设计归原权利人所有。本项目为非官方同人开源作品，代码采用 GPL-3.0 协议，行为设计参考 isHarryh/Ark-Pets。"
  },

  en: {
    pageTitle: "Si Companion (si_accompany v2.1.2) — Desktop Pet & Live Sync",
    langButton: "中文",
    "brand.name": "Si Companion",
    "nav.intro": "About",
    "nav.sync": "Live Sync",
    "nav.download": "Download",
    "nav.guide": "Beginner Guide",
    "nav.interactions": "Controls",
    "nav.faq": "FAQ",
    "nav.feedback": "Feedback",

    "hero.badge": "Windows 10/11 Portable Build · v2.1.2 Released",
    "hero.title": "Si · Your Lively Desktop Companion",
    "hero.subtitle":
      "No coding or Python required—just unzip and double-click. v2.1.2 upgrades 3D anti-clipping sprites, Soul Engine V1.2 persona, and smart headphone/game-aware TTS with spoken onomatopoeia and procedural action sound effects.",
    "hero.ctaDownload": "Download for Windows (v2.1.2)",
    "hero.ctaSync": "Live Chat & Data Sync",
    "hero.pill1": "✓ Portable ZIP, No Installer",
    "hero.pill2": "✓ Headphone & Game-Aware TTS",
    "hero.pill3": "✓ API Keys Stay in Memory Only",
    "hero.pill4": "✓ Free & Open Source (GPL-3.0)",
    "hero.previewTitle": "3D Live Stage · Move cursor for eye gaze",
    "hero.modeFull": "Bust View",
    "hero.modeBust": "Full 3D Live",

    "mood.happy": "Headpat",
    "mood.neutral": "Eye Gaze",
    "mood.sitting": "Sit Down",
    "mood.blush": "Snack",
    "mood.thinking": "Chin Pose",
    "mood.walk": "Stroll",
    "mood.sleepy": "Nap",

    "intro.kicker": "INTRODUCTION",
    "intro.title": "What is si_accompany (Si Companion v2.1.2)?",
    "intro.desc":
      "Si Companion (si_accompany) is an unofficial open-source fan-made desktop pet featuring Si from Arknights: Endfield, combining calm offline companionship with Soul Engine V1.2 AI chat, smart Foley TTS, and cross-device sync.",

    "feat1.title": "3D Anti-Clipping Art Overhaul",
    "feat1.desc":
      "v2.1.2 eliminates sitting/crouching skirt clipping and ships 5 crisp bust portraits alongside window-top perching and 16-angle eye tracking.",
    "feat2.title": "Smart Headphone TTS & Action SFX",
    "feat2.desc":
      "Outputs voice + text simultaneously when headphones are active and no competitive game (Apex, R6S, CS2, Valorant) is running, complete with onomatopoeia and brush/tea/stamp Foley SFX.",
    "feat3.title": "Soul Engine V1.2 Persona & Vision",
    "feat3.desc":
      "Fine-tuned character persona supporting local Ollama, DeepSeek Official API, and custom school/third-party OpenAI endpoints with drag-and-drop image chat.",
    "feat4.title": "Adaptive Ink-Jade Dark & Light Theme",
    "feat4.desc":
      "Panels, portrait chat bubbles, collapsible config drawers, and menus follow Windows light or dark mode seamlessly.",
    "feat5.title": "Local Storage & Privacy First",
    "feat5.desc":
      "All affinity data, settings, and chat logs stay inside the local data/ folder. API keys remain strictly in RAM and are never written to disk.",
    "feat6.title": "Cross-Device & WAN Chat Sync",
    "feat6.desc":
      "Syncs companion counters, journals, and chat turns across Windows, LAN TLS peers, Android preview, and this live web dashboard.",

    "sync.kicker": "LIVE SYNC",
    "sync.title": "Live Companion Status & Chat Sync",
    "sync.desc":
      "Synchronizes affinity, mood, companion journal, and recent chat turns with the desktop pet. When connected via the live WAN node, you can interact and chat with Si in real time.",
    "sync.connecting": "Checking Si live sync status…",
    "sync.liveOnline": "🟢 Live Connected · Synced with local Si ledger & Soul Engine V1.2",
    "sync.staticSnapshot": "🟡 Snapshot Mode (GitHub Pages Mirror) · Showing latest synced state",
    "sync.patBtn": "👋 Remote Headpat",
    "sync.snackBtn": "🍵 Send Snack",
    "sync.refreshBtn": "↻ Refresh",
    "sync.statAffinity": "Affinity",
    "sync.statMood": "Mood",
    "sync.statPats": "Headpats",
    "sync.statSnacks": "Snacks",
    "sync.statFocus": "Focus Done",
    "sync.statMinutes": "Companion Time",
    "sync.voiceDefault": "Smart TTS: Voice + Text active when headphones connected & no game running",
    "sync.chatTitle": "💬 Synced Chat Transcript",
    "sync.chatPlaceholder": "Say something to Si on the live node (syncs to desktop)…",
    "sync.chatSend": "Send",
    "sync.chatHint": "Tip: Live WAN tunnel enables real-time two-way chat with the host model; GitHub Pages displays the latest synced snapshot.",
    "sync.journalTitle": "📔 Companion Journal",
    "sync.emptyChat": "No synced chat turns yet—send a message to say hi!",
    "sync.emptyJournal": "Our companionship journal starts right here.",

    "dl.kicker": "DOWNLOAD",
    "dl.title": "Download Si Companion (v2.1.2)",
    "dl.desc":
      "If you're a beginner, simply download the <strong>Windows Portable ZIP</strong> below—no setup or coding tools required.",
    "dl.recommended": "Recommended for Beginners",
    "dl.winTitle": "SiDesktopPet · Windows Portable Edition",
    "dl.winDesc":
      "Unzip and run immediately. Bundles the full runtime, 3D anti-clipping sprites, portraits, smart headphone/game TTS + Foley SFX, and usage guide.",
    "dl.fileLabel": "File: ",
    "dl.sizeLabel": "Size: ",
    "dl.winBtn": "Download Windows (v2.1.2)",
    "dl.repoBtn": "View GitHub Repository",
    "dl.copy": "Copy",
    "dl.copied": "Copied ✓",
    "dl.androidTitle": "Android Preview (v0.1.3-preview)",
    "dl.androidDesc":
      "Mobile companion preview with overlay display and LAN TLS pairing sync with the Windows client (not for HarmonyOS 5).",
    "dl.androidLink": "View Android APK on Releases →",
    "dl.sourceTitle": "HarmonyOS 5 Preview & Full Source",
    "dl.sourceDesc":
      "Complete open-source repository for developers, including desktop source, Soul Engine dataset builders, QQ prototype, and HarmonyOS 5 preview.",
    "dl.sourceLink": "Browse Source on GitHub →",

    "guide.kicker": "STEP-BY-STEP GUIDE",
    "guide.title": "Beginner's 4-Step Quickstart",
    "guide.desc":
      "New to portable ZIP apps? Follow these 4 simple steps—you'll have Si on your desktop in under 2 minutes.",

    "step1.title": "Download & Extract the ENTIRE ZIP Folder",
    "step1.desc":
      "Click the download button above to get <code>SiDesktopPet-2.1.2-Windows-x64.zip</code>. Right-click the ZIP → choose <strong>Extract All...</strong> into a normal folder like <code>D:\\SiDesktopPet</code>.",
    "step1.warnTitle": "⚠️ Most Common Beginner Mistake: ",
    "step1.warnBody":
      "Always <strong>extract the full ZIP first</strong>! Do not double-click the EXE directly inside the ZIP preview window.",

    "step2.title": "Double-Click <code>Si-DesktopPet.exe</code> to Launch",
    "step2.desc":
      "Open the extracted folder and double-click <code>Si-DesktopPet.exe</code>. Both the Companion Panel and Si will appear right away!",
    "step2.tipTitle": '💡 Seeing a blue "Windows protected your PC" pop-up?',
    "step2.tipBody":
      'That is normal for unsigned indie open-source apps. Click <strong>"More info"</strong> → then click <strong>"Run anyway"</strong>.',

    "step3.title": "(Optional) Connect AI Chat, Vision & Smart Voice",
    "step3.desc":
      'Click "Free Chat" or "Multimodal Chat" in the panel to pick an AI backend; with VoiceStudio installed, plugging in headphones enables automatic voice + Foley SFX:',

    "tab.deepseek": "Option A: DeepSeek Official (Easiest)",
    "tab.custom": "Option B: School / Custom API",
    "tab.local": "Option C: Local Ollama (Offline)",
    "tab.deepseek1":
      '1. Sign up at <a href="https://platform.deepseek.com" target="_blank" rel="noopener noreferrer">platform.deepseek.com</a> and create an API key (starts with <code>sk-</code>).',
    "tab.deepseek2":
      "2. Choose <strong>DeepSeek Official API</strong>—endpoint and <code>deepseek-chat</code> fill in automatically.",
    "tab.deepseek3":
      "3. Paste your API key and start chatting. Your key stays in RAM only.",
    "tab.custom1": "1. Select <strong>Custom Model API (School / Third-Party)</strong>.",
    "tab.custom2":
      "2. Enter your OpenAI-compatible Base URL and paste your API key.",
    "tab.custom3":
      '3. Click <strong>"Fetch Models"</strong> to pick a model and drag & drop images in Multimodal Chat.',
    "tab.local1":
      "1. Start <strong>Ollama</strong> (port <code>11434</code>) or <strong>LM Studio</strong> (port <code>1234</code>).",
    "tab.local2": "2. Choose <strong>Local Model</strong> (defaults to <code>si-qwen2.5-3b-lang-lora:latest</code>).",
    "tab.local3": '3. Click "Fetch Models" and chat completely offline.',

    "step4.title": "Everyday Tips: Recover Pet, Exit & Backup",
    "step4.tip1":
      '<strong>Can\'t find Si?</strong> Press <code>Ctrl + Alt + S</code>, or right-click the tray icon and choose <strong>"Find Si"</strong>.',
    "step4.tip2":
      "<strong>How to exit completely?</strong> Right-click Si or the tray icon and select Quit.",
    "step4.tip3":
      "<strong>Upgrading?</strong> Quit the old version first, then copy <code>data/</code> into the new folder.",

    "inter.kicker": "CONTROLS",
    "inter.title": "How to Interact with Si",
    "inter.desc": "A quick cheat sheet of mouse, keyboard, and smart voice features.",
    "inter.k1": "Left-Click / Headpat",
    "inter.v1": "Poke or pat Si for a wave, a character line, and extra affinity.",
    "inter.k2": "Drag & Release",
    "inter.v2": "Move Si across monitors or onto window titlebars—she drops with gentle gravity.",
    "inter.k3": "Double-Click",
    "inter.v3": "Make Si do a playful hop in place.",
    "inter.k4": "Right-Click",
    "inter.v4": "Switch actions (walk, sit, nap, wave), open the panel, or quit.",
    "inter.k5": "Smart Headphone TTS",
    "inter.v5": "Auto-speaks with Foley SFX on headphones; auto-mutes when Apex / R6S / games launch.",
    "inter.v6": "Instantly recalls Si to your main screen and disables click-through.",

    "faq.kicker": "FAQ",
    "faq.title": "Frequently Asked Questions",
    "faq.q1": "I don't know how to code and don't have Python installed. Can I still use it?",
    "faq.a1":
      "Yes! Extract the Windows portable ZIP and double-click Si-DesktopPet.exe—no Python needed.",
    "faq.q2": "Will Si interrupt my audio while playing Apex Legends or Rainbow Six Siege?",
    "faq.a2":
      "Never! v2.1.2 checks active audio endpoints and local game processes in real time: voice only plays on headphones when no game is running, and mutes immediately if a game starts.",
    "faq.q3": 'Windows says "Windows protected your PC". Is it safe?',
    "faq.a3":
      'Yes. Click "More info" → "Run anyway", and verify the SHA-256 hash above.',
    "faq.q4": "Are my API key and chat logs private?",
    "faq.a4":
      "All history stays in your local data/ folder; API keys stay strictly in RAM.",
    "faq.q5": "What if I turn on Click-Through and can't click Si anymore?",
    "faq.a5":
      'Press Ctrl + Alt + S or use the system tray icon to restore normal clicking.',

    "fb.kicker": "GET IN TOUCH",
    "fb.title": "Feedback (意见反馈)",
    "fb.desc":
      "Found a bug or have a feature idea? Submit below to send directly to <strong>2477160494@qq.com</strong>.",
    "fb.typeLabel": "Feedback Type",
    "fb.typeSuggestion": "Feature Suggestion",
    "fb.typeBug": "Bug Report",
    "fb.typeHelp": "Beginner Help / Question",
    "fb.typeOther": "Other",
    "fb.platformLabel": "Version / Platform",
    "fb.platformNone": "Not downloaded yet / General inquiry",
    "fb.nameLabel": "Your Name (Optional)",
    "fb.namePlaceholder": "e.g., Administrator / Alex",
    "fb.contactLabel": "Your Email or QQ (Optional)",
    "fb.contactPlaceholder": "e.g., you@example.com",
    "fb.msgLabel": "Your Message",
    "fb.msgPlaceholder": "Describe the issue or share your suggestion...",
    "fb.submitBtn": "Send Directly to Email",
    "fb.mailtoBtn": "Open in Email App",
    "fb.directLabel": "Or email directly:",
    "fb.sending": "Sending your feedback to 2477160494@qq.com...",
    "fb.emptyMsg": "Please enter your feedback message before sending.",
    "fb.success": "✅ Feedback sent to 2477160494@qq.com! Thank you.",
    "fb.fallback":
      '⚠️ Direct delivery unavailable here. Please click "Open in Email App" or email 2477160494@qq.com.',

    "footer.disclaimer":
      "Si and Arknights: Endfield belong to their respective copyright holders. Unofficial open-source fan project under GPL-3.0."
  }
};

// State
let currentLang = localStorage.getItem("si_lang") === "en" ? "en" : "zh";
let currentMood = "happy";
let liveSyncOnline = false;
let lastSyncData = null;

// Optional ?api=https://... query parameter so GitHub Pages can also bind to a live WAN tunnel
const urlParams = new URLSearchParams(window.location.search);
const apiOverride = (urlParams.get("api") || "").replace(/\/+$/, "");

function apiUrl(path) {
  return apiOverride ? `${apiOverride}${path}` : path;
}

// Elements
const langToggle = document.getElementById("langToggle");
const langLabel = document.getElementById("langLabel");
const themeToggle = document.getElementById("themeToggle");
const themeIcon = document.getElementById("themeIcon");
const menuToggle = document.getElementById("menuToggle");
const navLinks = document.getElementById("navLinks");
const mascotImg = document.getElementById("mascotImg");
const mascotBubble = document.getElementById("mascotBubble");
const moodButtons = document.querySelectorAll(".mood-btn");
const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanels = document.querySelectorAll(".tab-panel");
const copyShaBtn = document.getElementById("copyShaBtn");
const copyEmailBtn = document.getElementById("copyEmailBtn");
const feedbackForm = document.getElementById("feedbackForm");
const fbSubmitBtn = document.getElementById("fbSubmitBtn");
const fbMailtoBtn = document.getElementById("fbMailtoBtn");
const fbStatus = document.getElementById("fbStatus");

// Sync UI Elements
const syncDot = document.getElementById("syncDot");
const syncModeLabel = document.getElementById("syncModeLabel");
const syncUpdatedAt = document.getElementById("syncUpdatedAt");
const syncPatBtn = document.getElementById("syncPatBtn");
const syncSnackBtn = document.getElementById("syncSnackBtn");
const syncRefreshBtn = document.getElementById("syncRefreshBtn");
const syncChatLog = document.getElementById("syncChatLog");
const syncChatForm = document.getElementById("syncChatForm");
const syncChatInput = document.getElementById("syncChatInput");
const syncChatSend = document.getElementById("syncChatSend");
const syncJournalList = document.getElementById("syncJournalList");
const syncVoiceText = document.getElementById("syncVoiceText");
const syncDeviceBadge = document.getElementById("syncDeviceBadge");

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = String(str ?? "");
  return div.innerHTML;
}

function renderSyncData(data) {
  if (!data) return;
  lastSyncData = data;
  liveSyncOnline = Boolean(data.live);
  const dict = I18N[currentLang];

  syncDot.className = liveSyncOnline ? "sync-dot online" : "sync-dot snapshot";
  syncModeLabel.textContent = liveSyncOnline ? dict["sync.liveOnline"] : dict["sync.staticSnapshot"];
  syncUpdatedAt.textContent = data.updated_at ? `(${data.updated_at})` : "";
  if (data.device_id) {
    syncDeviceBadge.textContent = `device:${data.device_id.slice(0, 8)}`;
  }

  const s = data.stats || {};
  document.getElementById("statAffinity").textContent = s.affinity ?? 0;
  document.getElementById("statMood").textContent = s.mood ?? 85;
  document.getElementById("statPats").textContent = s.pats ?? 0;
  document.getElementById("statSnacks").textContent = s.snacks ?? 0;
  document.getElementById("statFocus").textContent = s.focus_sessions ?? 0;
  document.getElementById("statMinutes").textContent = `${s.total_minutes ?? 0} min`;

  if (data.voice && data.voice.summary) {
    syncVoiceText.textContent = data.voice.summary;
  }

  // Interactive controls enabled only when connected to live sync server
  syncPatBtn.disabled = !liveSyncOnline;
  syncSnackBtn.disabled = !liveSyncOnline;
  syncChatInput.disabled = !liveSyncOnline;
  syncChatSend.disabled = !liveSyncOnline;

  const turns = Array.isArray(data.turns) ? data.turns : [];
  if (!turns.length) {
    syncChatLog.innerHTML = `<div class="sync-empty">${escapeHtml(dict["sync.emptyChat"])}</div>`;
  } else {
    syncChatLog.innerHTML = turns
      .map(
        (t) => `
        <div class="chat-pair">
          <div class="chat-msg chat-user"><span class="chat-role">管理员：</span>${escapeHtml(t.user)}</div>
          <div class="chat-msg chat-si"><img src="assets/happy.png" alt="" class="chat-mini-av" /><div><span class="chat-role si">祀：</span>${escapeHtml(t.assistant)}</div></div>
        </div>`
      )
      .join("");
    syncChatLog.scrollTop = syncChatLog.scrollHeight;
  }

  const journal = Array.isArray(data.journal) ? data.journal : [];
  if (!journal.length) {
    syncJournalList.innerHTML = `<li class="sync-empty">${escapeHtml(dict["sync.emptyJournal"])}</li>`;
  } else {
    syncJournalList.innerHTML = journal
      .map(
        (j) => `<li><span class="j-time">${escapeHtml(j.at)}</span><span class="j-text">${escapeHtml(j.text)}</span></li>`
      )
      .join("");
  }
}

async function refreshSyncStatus() {
  try {
    const res = await fetch(apiUrl("/api/sync-status"), { cache: "no-store" });
    if (res.ok) {
      const data = await res.json();
      renderSyncData(data);
      return;
    }
  } catch {
    // Fall back to static snapshot on GitHub Pages
  }
  try {
    const snapRes = await fetch("sync-snapshot.json", { cache: "no-cache" });
    if (snapRes.ok) {
      const snap = await snapRes.json();
      snap.live = false;
      renderSyncData(snap);
    }
  } catch {
    // Ignore if neither is present
  }
}

async function sendCompanionAction(action) {
  if (!liveSyncOnline) return;
  try {
    const res = await fetch(apiUrl("/api/interact"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action })
    });
    if (res.ok) {
      const out = await res.json();
      if (out.snapshot) renderSyncData(out.snapshot);
    }
  } catch {
    // Ignore transient network hiccup
  }
}

syncPatBtn.addEventListener("click", () => sendCompanionAction("pat"));
syncSnackBtn.addEventListener("click", () => sendCompanionAction("snack"));
syncRefreshBtn.addEventListener("click", () => refreshSyncStatus());

syncChatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!liveSyncOnline) return;
  const msg = syncChatInput.value.trim();
  if (!msg) return;
  syncChatSend.disabled = true;
  syncChatInput.disabled = true;
  const oldBtnText = syncChatSend.textContent;
  syncChatSend.textContent = "…";
  try {
    const res = await fetch(apiUrl("/api/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg })
    });
    const out = await res.json();
    if (res.ok && out.ok) {
      syncChatInput.value = "";
      if (out.snapshot) renderSyncData(out.snapshot);
      if (out.reply) mascotBubble.textContent = out.reply.replace(/\|\|/g, " ").slice(0, 90);
    } else if (out.error) {
      alert(out.error);
    }
  } catch {
    alert("实时节点连接超时，请稍后再试。");
  } finally {
    syncChatSend.textContent = oldBtnText;
    syncChatSend.disabled = !liveSyncOnline;
    syncChatInput.disabled = !liveSyncOnline;
    if (liveSyncOnline) syncChatInput.focus();
  }
});

// Apply Language
function applyLanguage(lang) {
  currentLang = lang;
  localStorage.setItem("si_lang", lang);
  const dict = I18N[lang];
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  document.title = dict.pageTitle;
  langLabel.textContent = dict.langButton;

  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (dict[key] !== undefined) {
      el.innerHTML = dict[key];
    }
  });

  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (dict[key] !== undefined) {
      el.setAttribute("placeholder", dict[key]);
    }
  });

  mascotBubble.textContent = MOODS[currentMood][lang];
  const sBtn = document.getElementById("stageModeBtn");
  if (sBtn && typeof stageFullBody !== "undefined") {
    sBtn.textContent = stageFullBody ? dict["hero.modeFull"] : dict["hero.modeBust"];
  }
  if (lastSyncData) renderSyncData(lastSyncData);
}

// Apply Theme
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("si_theme", theme);
  themeIcon.textContent = theme === "dark" ? "☀️" : "🌙";
}

function initTheme() {
  const saved = localStorage.getItem("si_theme");
  if (saved === "dark" || saved === "light") {
    applyTheme(saved);
  } else {
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(prefersDark ? "dark" : "light");
  }
}

langToggle.addEventListener("click", () => {
  applyLanguage(currentLang === "zh" ? "en" : "zh");
});

themeToggle.addEventListener("click", () => {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  applyTheme(isDark ? "light" : "dark");
});

menuToggle.addEventListener("click", () => {
  const isOpen = navLinks.classList.toggle("open");
  menuToggle.setAttribute("aria-expanded", String(isOpen));
});

navLinks.querySelectorAll("a").forEach((link) => {
  link.addEventListener("click", () => {
    navLinks.classList.remove("open");
    menuToggle.setAttribute("aria-expanded", "false");
  });
});

// Live 3D Full-Body Sprite Stage & 16-Angle Gaze Engine
const mascotStage = document.getElementById("mascotStage");
const mascotWrap = document.getElementById("mascotWrap");
const mascotBustBadge = document.getElementById("mascotBustBadge");
const mascotBustImg = document.getElementById("mascotBustImg");
const stageModeBtn = document.getElementById("stageModeBtn");

let stageFullBody = true;
let animTimer = null;
let animFrameIdx = 0;
let animLoopsDone = 0;
let settledIdle = false;
let gazeLookIdx = null;
let nextBlinkAt = performance.now() + 3400;
let blinkStep = -1;
const preloadedStates = new Set();

function spritePath(state, idx) {
  return `assets/sprites/${state}/${String(idx).padStart(2, "0")}.webp`;
}

function preloadState(state, count) {
  if (preloadedStates.has(state)) return;
  preloadedStates.add(state);
  for (let i = 0; i < count; i++) {
    const im = new Image();
    im.src = spritePath(state, i);
  }
}

function updateStageModeUI() {
  const dict = I18N[currentLang];
  if (stageModeBtn) {
    stageModeBtn.textContent = stageFullBody ? dict["hero.modeFull"] : dict["hero.modeBust"];
  }
  if (mascotBustBadge) {
    mascotBustBadge.style.display = stageFullBody ? "block" : "none";
  }
  if (!stageFullBody) {
    if (animTimer) {
      clearInterval(animTimer);
      animTimer = null;
    }
    mascotImg.className = "mascot-img portrait-only";
    mascotImg.src = MOODS[currentMood].img;
  } else {
    startMoodStage(currentMood);
  }
}

function startMoodStage(mood) {
  const spec = MOODS[mood] || MOODS.happy;
  if (mascotBustImg) mascotBustImg.src = spec.img;
  if (!stageFullBody) {
    mascotImg.className = "mascot-img portrait-only";
    mascotImg.src = spec.img;
    return;
  }
  preloadState(spec.state, spec.frames);
  preloadState("idle", 6);
  if (animTimer) clearInterval(animTimer);
  animFrameIdx = 0;
  animLoopsDone = 0;
  settledIdle = spec.loop === "idle_gaze";
  blinkStep = -1;
  nextBlinkAt = performance.now() + 2600 + Math.random() * 2400;

  mascotImg.className =
    spec.state === "sleeping"
      ? "mascot-img sleep-breathe"
      : spec.state === "walk-right"
      ? "mascot-img no-breathe"
      : "mascot-img";

  mascotImg.src = settledIdle ? spritePath("idle", 0) : spritePath(spec.state, 0);

  const intervalMs = Math.round(1000 / (spec.fps || 6));
  animTimer = setInterval(() => {
    if (document.hidden || !stageFullBody) return;
    const now = performance.now();

    if (settledIdle) {
      // Periodic natural anime blink (frames 1 -> 2 -> 3 -> 0) or gentle breathe (4 -> 5 -> 0)
      if (blinkStep >= 0) {
        const seq = [1, 2, 3, 0];
        mascotImg.src = spritePath("idle", seq[Math.min(seq.length - 1, blinkStep)]);
        blinkStep++;
        if (blinkStep >= seq.length) {
          blinkStep = -1;
          nextBlinkAt = now + 3200 + Math.random() * 3200;
        }
        return;
      }
      if (now >= nextBlinkAt) {
        if (Math.random() < 0.72) {
          blinkStep = 0;
          mascotImg.src = spritePath("idle", 1);
          return;
        } else {
          // Soft chest breath frame
          mascotImg.src = spritePath("idle", 4);
          nextBlinkAt = now + 2800 + Math.random() * 2600;
          return;
        }
      }
      if (gazeLookIdx !== null) {
        mascotImg.src = spritePath("look", gazeLookIdx);
      } else {
        mascotImg.src = spritePath("idle", 0);
      }
      return;
    }

    animFrameIdx++;
    if (animFrameIdx >= spec.frames) {
      animLoopsDone++;
      if (spec.loop === "infinite") {
        animFrameIdx = 0;
      } else if (typeof spec.loop === "number") {
        if (animLoopsDone >= spec.loop) {
          settledIdle = true;
          mascotImg.src = spritePath("idle", 0);
          return;
        }
        animFrameIdx = 0;
      } else if (spec.loop === "hold_last") {
        animFrameIdx = spec.frames - 1;
        clearInterval(animTimer);
        animTimer = null;
        mascotImg.src = spritePath(spec.state, animFrameIdx);
        return;
      }
    }
    mascotImg.src = spritePath(spec.state, animFrameIdx);
  }, intervalMs);
}

if (stageModeBtn) {
  stageModeBtn.addEventListener("click", () => {
    stageFullBody = !stageFullBody;
    updateStageModeUI();
  });
}

if (mascotStage && mascotWrap) {
  mascotStage.addEventListener("mousemove", (e) => {
    if (!stageFullBody || !settledIdle) return;
    preloadState("look", 16);
    const rect = mascotWrap.getBoundingClientRect();
    const headX = rect.left + rect.width * 0.5;
    const headY = rect.top + rect.height * 0.24;
    const dx = e.clientX - headX;
    const dy = e.clientY - headY;
    if (Math.hypot(dx, dy) < 18) {
      gazeLookIdx = null;
      return;
    }
    const deg = ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360;
    gazeLookIdx = Math.floor((deg + 11.25) / 22.5) % 16;
  });

  mascotStage.addEventListener("mouseleave", () => {
    gazeLookIdx = null;
  });

  mascotWrap.addEventListener("click", () => {
    const patBtn = document.querySelector('.mood-btn[data-mood="happy"]');
    if (patBtn) patBtn.click();
  });
}

moodButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const mood = btn.getAttribute("data-mood");
    if (!MOODS[mood]) return;
    currentMood = mood;
    moodButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    mascotBubble.textContent = MOODS[mood][currentLang];
    startMoodStage(mood);
  });
});

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.getAttribute("data-tab");
    tabButtons.forEach((b) => {
      const active = b === btn;
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", String(active));
    });
    tabPanels.forEach((panel) => {
      const match = panel.id === `tab-${target}`;
      panel.classList.toggle("active", match);
      panel.hidden = !match;
    });
  });
});

async function copyText(text, buttonEl) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const tmp = document.createElement("textarea");
    tmp.value = text;
    document.body.appendChild(tmp);
    tmp.select();
    document.execCommand("copy");
    document.body.removeChild(tmp);
  }
  const originalKey = buttonEl.getAttribute("data-i18n");
  buttonEl.textContent = I18N[currentLang]["dl.copied"];
  setTimeout(() => {
    buttonEl.textContent = I18N[currentLang][originalKey];
  }, 1800);
}

copyShaBtn.addEventListener("click", () => copyText(CONFIG.sha256, copyShaBtn));
copyEmailBtn.addEventListener("click", () => copyText(CONFIG.feedbackEmail, copyEmailBtn));

function getFeedbackPayload() {
  return {
    type: document.getElementById("fbType").value,
    platform: document.getElementById("fbPlatform").value,
    name: document.getElementById("fbName").value.trim() || "Anonymous",
    contact: document.getElementById("fbContact").value.trim() || "Not provided",
    message: document.getElementById("fbMessage").value.trim()
  };
}

function showStatus(type, text) {
  fbStatus.hidden = false;
  fbStatus.className = `fb-status ${type}`;
  fbStatus.textContent = text;
}

function openMailtoFallback(payload) {
  const subject = `[si_accompany 意见反馈] ${payload.type} (${payload.platform})`;
  const body = [
    `反馈类型 / Type: ${payload.type}`,
    `版本平台 / Platform: ${payload.platform}`,
    `称呼 / Name: ${payload.name}`,
    `联系方式 / Contact: ${payload.contact}`,
    "",
    "反馈内容 / Message:",
    payload.message || "(请在此填写内容)"
  ].join("\n");

  const href = `mailto:${CONFIG.feedbackEmail}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  window.location.href = href;
}

fbMailtoBtn.addEventListener("click", () => {
  const payload = getFeedbackPayload();
  openMailtoFallback(payload);
});

feedbackForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const dict = I18N[currentLang];
  const payload = getFeedbackPayload();

  if (!payload.message) {
    showStatus("error", dict["fb.emptyMsg"]);
    document.getElementById("fbMessage").focus();
    return;
  }

  fbSubmitBtn.disabled = true;
  showStatus("success", dict["fb.sending"]);

  try {
    const response = await fetch(CONFIG.formSubmitEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json"
      },
      body: JSON.stringify({
        _subject: `[si_accompany 意见反馈] ${payload.type} - ${payload.platform}`,
        _template: "table",
        "反馈类型 (Type)": payload.type,
        "版本平台 (Platform)": payload.platform,
        "称呼 (Name)": payload.name,
        "联系方式 (Contact)": payload.contact,
        "反馈内容 (Message)": payload.message
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const result = await response.json();
    if (result.success === "true" || result.success === true) {
      showStatus("success", dict["fb.success"]);
      feedbackForm.reset();
    } else {
      throw new Error("FormSubmit did not confirm success");
    }
  } catch {
    showStatus("error", dict["fb.fallback"]);
  } finally {
    fbSubmitBtn.disabled = false;
  }
});

// Initialize
initTheme();
applyLanguage(currentLang);
startMoodStage(currentMood);
refreshSyncStatus();
setInterval(() => {
  if (liveSyncOnline) refreshSyncStatus();
}, 6000);
