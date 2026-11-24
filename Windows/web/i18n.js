import {gameTerms} from './i18n-game-terms.js';
import {uiEnglish, uiTemplates} from './i18n-ui-en.js';
import {styleRules} from './playing-style-rules.js';

const STORAGE_KEY = 'studio-lang';
const origText = new WeakMap();
const origAttrs = new WeakMap();
const ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'label'];

const EXACT = {
  '比赛手感': 'Match Feel',
  '选择原版、工坊版或自定义比赛调校。': 'Choose original, workshop, or custom match tuning.',
  '保存比赛手感': 'Save Match Feel',
  '比赛手感已保存': 'Match feel saved',
  '比赛手感档位': 'Match feel preset',
  '原版': 'Original',
  '工坊版': 'Workshop',
  '恢复原版比赛常量与动作。搓射旋转 ×1.50，旋转量达到 0.70 时生效。': 'Restore original match constants and motions. Finesse-shot spin is multiplied by 1.50 when the release spin reaches 0.70.',
  '沿用之前工坊的 V8 比赛调校。搓射旋转 ×1.75，门槛 0.50。': 'Use the previous Workshop V8 tuning, with a 1.75 finesse-shot spin multiplier and a 0.50 threshold.',
  '按原版和工坊参考值，直接填写要用的数值。': 'Enter the values to use alongside the Original and Workshop references.',
  '保存后，重新生成并安装游戏 APK 生效。': 'After saving, rebuild and install the game APK to apply the changes.',
  '当前来源需要使用 V17 比赛适配。': 'This feature requires a game source supported by the V17 adapter.',
  '原版参考值': 'Original',
  '工坊参考值': 'Workshop',
  '恢复自定义默认值': 'Restore Custom Defaults',
  '搓射的原版参考值来自工坊的「原版」档。': 'The finesse-shot references use the workshop’s Original preset.',
  '已保留旧版比例设置中的其他参数。恢复自定义默认值会一并清除它们。': 'Other values from your previous tuning were preserved. Restore Custom Defaults clears those values too.',
  '接球与转身': 'Receiving and Turning',
  '本方地滚球接球反应时间': 'Reaction Time to a Teammate’s Ground Ball',
  '停球输入窗口': 'Trap Input Window',
  '停球动作取消帧': 'Trap Animation Cancel Frame',
  '接球打开身体最大值': 'Maximum Open Body Angle',
  '地面传球调校': 'Ground Pass Tuning',
  '地面传球速度下限系数': 'Minimum Ground Pass Speed Multiplier',
  '地面传球速度上限系数': 'Maximum Ground Pass Speed Multiplier',
  '长传与传中': 'Lofted Passes and Crosses',
  '长传满蓄力距离': 'Lofted Pass Distance at Full Power',
  '传中速度修正': 'Cross Speed Adjustment',
  '球的飞行、滚动与反弹': 'Ball Flight, Roll and Bounce',
  '空中弧线系数': 'In-flight Curve Coefficient',
  '自然滚动系数': 'Natural Roll Coefficient',
  '停球速度门槛': 'Ball Stop Speed Threshold',
  '反弹系数 [0]': 'Bounce Coefficient [0]',
  '反弹摩擦速度上限': 'Maximum Bounce Friction Speed',
  '搓射旋转倍率': 'Finesse Shot Spin Multiplier',
  '出球旋转门槛': 'Release Spin Threshold',
  '工坊通用传控与射门修正': 'Workshop Passing, Control and Shooting Adjustments',
  '应用已启用的工坊 AI 设置': 'Apply Enabled Workshop AI Settings',
  // Brand, shell, dock, sidebar, dialogs
  '球员工坊': 'Player Studio',
  '球员工坊 · PLAYER STUDIO': 'Player Studio',
  '本地工作区': 'Local Workspace',
  '全部功能': 'All Features',
  '全部菜单': 'All Menus',
  '关闭菜单': 'Close Menu',
  '切换完整导航': 'Full Navigation',
  '切换精简导航': 'Compact Navigation',
  '永久工程身份': 'Permanent Project ID',
  '正在读取': 'Loading',
  '正在读取…': 'Loading…',
  '正在打开本地工程…': 'Opening local project…',
  '使用说明': 'User Guide',
  '使用声明': 'Disclaimer',
  '我已了解，进入软件': 'I Understand, Enter',
  '开始创作': 'Start Creating',
  '回到总览': 'Back to Overview',
  '快速跳转': 'Quick Jump',
  '快速跳转 · Ctrl K': 'Quick Jump · Ctrl K',
  '搜索页面，回车跳转': 'Search pages, press Enter to jump',
  '搜索页面': 'Search pages',
  '没有匹配的页面': 'No matching pages',
  '先读取或新建球员': 'Load or create a player first',
  '当前页面': 'Current page',
  '帮助': 'Help',
  '工作区': 'Workspace',
  '切换工作区': 'Switch Workspace',
  '分区': 'Sections',
  '工作区导航': 'Workspace Navigation',
  '当前分区页面': 'Section Pages',
  '关闭': 'Close',
  '取消': 'Cancel',
  '确认': 'Confirm',
  '保存': 'Save',
  '刷新': 'Refresh',
  '删除': 'Delete',
  '编辑': 'Edit',
  '复制': 'Duplicate',
  '导出': 'Export',
  '导入': 'Import',
  '重置': 'Reset',
  '清空': 'Clear',
  '搜索': 'Search',
  '全部': 'All',
  '返回': 'Back',
  '下一步': 'Next',
  '上一步': 'Previous',
  '完成': 'Done',
  '继续': 'Continue',
  '应用': 'Apply',
  '选择': 'Select',
  '已选择': 'Selected',
  '未选择': 'Not selected',
  '查看全部': 'View All',

  // Top bar actions & theme/lang toggles
  '已保存到本机': 'Saved locally',
  '有未保存修改': 'Unsaved changes',
  '正在保存…': 'Saving…',
  '保存失败': 'Save failed',
  '撤销': 'Undo',
  '重做': 'Redo',
  '打开工程': 'Open Project',
  '导出工程': 'Export Project',
  '保存球员': 'Save Player',
  '新建球员': 'New Player',
  '浅色': 'Light',
  '深色': 'Dark',
  '极简浅色': 'Apple Light',
  '液态玻璃': 'Liquid Glass',
  '切换界面风格': 'Switch UI Theme',
  '切换语言': 'Switch Language',

  // Sections & Page titles
  '总览': 'Overview',
  '开始使用': 'Get Started',
  '快速开始': 'Quick Start',
  '详细教程': 'Detailed Guide',
  '新手引导': 'Getting Started',
  '游戏来源与更新': 'Game Source & Updates',
  '导出与游戏设置': 'Exports & Game Settings',
  '管理导出文件、直接安装及 AI 组别进度。': 'Manage exported files, installs, and AI division progress.',
  '画质、帧率与实体手柄': 'Graphics, FPS & Controller',
  '自定义前台帧率上限': 'Custom Foreground FPS Cap',
  '自定义游戏渲染分辨率': 'Custom Render Resolution',
  '将实体手柄改键写入游戏 APK': 'Write Controller Remapping to APK',
  '教练预览': 'Coach Preview',
  '不使用教练': 'No Coach',
  '当前显示球员自身与增能效果': 'Showing player base & booster stats',
  '浏览 / 新建教练': 'Browse / Create Coach',
  'AI 组别随机组队与临场战术（V17）': 'AI Division Random Squads & Tactics (V17)',
  'AI 随机阵容包含已实装 DIY 球员': 'Include Installed DIY Players in AI Squads',
  'AI 独狼与射门技能（V17）': 'AI Soloist & Shooting Skills (V17)',
  '我的球员': 'My Players',
  '原版球员库': 'Native Players',
  '球员与原版资源库': 'Players & Native Assets',
  '编辑球员': 'Edit Player',
  '资料': 'Profile',
  '球员资料': 'Player Profile',
  '能力': 'Attributes',
  '能力值': 'Attributes',
  '基础能力': 'Base Attributes',
  '培养': 'Player Progression',
  '培养模拟': 'Training Simulator',
  '技能': 'Skills',
  '技能与风格': 'Skills & Style',
  '动作': 'Motion',
  '庆祝与动作': 'Motion & Celebrations',
  '卡面': 'Card',
  '卡面设计': 'Card Design',
  '模型': '3D Model',
  '3D 模型': '3D Model',
  '脸模与体型': 'Face & Physique',
  '卡包与教练': 'Packs & Coaches',
  '卡包': 'Packs',
  '卡包编辑': 'Pack Editor',
  '教练': 'Coaches',
  '教练管理': 'Coach Manager',
  '球场与赛事': 'Stadium & Events',
  '球场': 'Stadium',
  '球场编辑': 'Stadium Editor',
  '赛事': 'Tournaments',
  '锦标赛': 'Tournaments',
  '生成安装': 'Build & Install',
  'APK 实装': 'Build APK',
  '生成游戏 APK': 'Build Game APK',
  '游戏源': 'Game Source',
  '导入游戏资源库': 'Import Game Library',
  '资源库准备中': 'Preparing Library',
  '更多工具': 'More Tools',
  '工具与设置': 'Tools & Settings',
  '工具': 'Tools',

  // Hub & Stage
  '球员卡': 'Player Card',
  '球员卡展台': 'Player Card Stage',
  '设计工程': 'Project Draft',
  '查看球员卡': 'View Player Card',
  '能力概览': 'Stats Overview',
  '导出卡面 PNG': 'Export Card PNG',
  '打开模型工作区': 'Open 3D Model Workspace',
  '左键旋转 · 滚轮缩放 · 右键平移': 'Left drag: rotate · Scroll: zoom · Right drag: pan',
  '暂停原版动画': 'Pause Animation',
  '播放原版动画': 'Play Animation',
  '尚未选择游戏原版图像': 'No native game artwork selected',
  '请先从球员与原版资源库选择': 'Choose one from Players & Native Assets first',
  '未命名球员': 'Unnamed Player',
  '未命名': 'Unnamed',
  '打造你的传奇球员': 'Create Your Own Legend',
  '继续编辑': 'Continue Editing',
  '剩余培养点': 'Remaining Points',
  '游戏准备': 'Game Setup',
  '选择球员、教练与卡包，写入游戏安装包': 'Pick players, coaches & packs to build into the game APK',
  '保存、分组与导出自建球员': 'Save, organize, and export custom players',
  '混编原版与自建球员，设置抽取权重': 'Mix native & custom players with draw weights',
  '原版与自建教练、战术加成': 'Native & custom coaches with tactical boosts',
  '导入新版游戏，刷新资源库': 'Import a newer game APK to refresh assets',
  '导出文件、直接安装与 AI 进度': 'Exports, storage cleanup, install & AI division',
  '正在读取球员数据库与原版资源，完成后即可生成游戏 APK': 'Loading player database and native assets. You can build APKs once ready.',
  '选择游戏 APK 与离线资源包，读取球员数据库与原版资源': 'Select the game APK and offline assets to load players and resources.',
  '从原版球员复制或新建一名球员，编辑能力、技能与卡面，再生成可安装的游戏 APK。': 'Copy a native player or create a new one, customize stats, skills & card art, then build an installable APK.',
  '先导入游戏资源库，再从原版球员复制或新建一名球员，编辑能力、技能与卡面，最后生成可安装的游戏 APK。': 'Import the game library first, then create or copy a player, customize stats & card art, and build an installable APK.',
  '从名字到球场位置，定义属于你的球员。': 'Define your player from name to pitch position.',
  '总评包含所选教练的已配置加成，不含状态箭头。APK 实装已接入；完整材质动画、脸模与比赛效果仍待游戏内验收。': 'OVR includes the manager bonus, without form arrows. Check APK materials, faces and match effects in-game.',

  // Player Profile & Identity
  '基本信息': 'Basic Info',
  '球员姓名': 'Player Name',
  '显示名称': 'Display Name',
  '球衣背印': 'Shirt Name',
  '球衣号码': 'Shirt Number',
  '国籍': 'Nationality',
  '国籍 / 地区': 'Nationality / Region',
  '俱乐部': 'Club',
  '所属球队': 'Club / Team',
  '联赛': 'League',
  '注册位置': 'Registered Position',
  '位置': 'Position',
  '擅长位置': 'Position Proficiency',
  '惯用脚': 'Preferred Foot',
  '右脚': 'Right',
  '左脚': 'Left',
  '年龄': 'Age',
  '身高': 'Height',
  '身高 (cm)': 'Height (cm)',
  '体重': 'Weight',
  '体重 (kg)': 'Weight (kg)',
  '球员类型': 'Card Type',
  '卡片类型': 'Card Type',
  '卡种': 'Card Series',
  '初始等级': 'Initial Level',
  '最高等级': 'Max Level',
  '等级上限': 'Max Level',
  '状态持续性': 'Form',
  '弱脚使用频率': 'Weak Foot Usage',
  '弱脚精准度': 'Weak Foot Accuracy',
  '抗受伤程度': 'Injury Resistance',
  '比赛风格': 'Playing Style',
  '风格': 'Style',
  '球员风格': 'Playing Style',
  '体型与外观': 'Physique & Appearance',
  '体型': 'Physique',
  '肤色': 'Skin Tone',
  '球鞋': 'Boots',
  '球鞋型号': 'Boots Model',
  '守门员手套': 'Goalkeeper Gloves',
  '仅门将（GK）位置可选': 'Goalkeeper (GK) only',
  '仅当主要位置为门将（GK）时可选择手套。': 'Only available when Registered Position is GK.',
  '球衣样式与比赛装备': 'Kit Style & Match Equipment',
  '球衣样式 / 袖长': 'Kit Style / Sleeve Length',
  '跟随模板球员': 'Follow Template Player',
  '每名球员独立保存': 'Saved per player',
  '袖长、球袜长度、球鞋与门将手套（仅 GK）随这名球员的工程保存，并在生成 APK 时写入比赛装备。头模窗口只展示头部，装备效果请在游戏中查看。': 'Sleeve length, sock length, boots, and GK gloves (GK only) are saved per player and written on APK export. The 3D preview shows only the head; check match gear in-game.',
  '所有装备与护具默认跟随模板球员，未改动的位段逐字节保留；护腕、下摆、紧身衣裤、脚踝绷带与冬季手套同时支持完整 APK 与 .73pack 小包导出。头模窗口只展示头部，装备效果请在游戏中查看。': 'All gear follows the template player by default, keeping untouched bits byte-identical. Wrist tape, shirt tail, inner wear, ankle tape, and winter gloves work in both full APK and .73pack exports. The 3D preview shows only the head; check match gear in-game.',
  '球衣下摆穿法': 'Shirt Tail',
  '扎进裤子': 'Tucked In',
  '放出来': 'Untucked',
  '紧身打底裤': 'Undershorts / Tights',
  '短款紧身裤': 'Short Tights',
  '长款紧身裤': 'Long Tights',
  '紧身内衣': 'Undershirt / Inner',
  '常规领': 'Standard Neck',
  '高领': 'High Neck',
  '左手护腕 / 绷带': 'Left Wrist Tape',
  '右手护腕 / 绷带': 'Right Wrist Tape',
  '不佩戴': 'Off',
  '佩戴': 'On',
  '白色绷带': 'White',
  '球衣队色': 'Team Color',
  '黑色绷带': 'Black',
  '浅蓝绷带': 'Sky Blue',
  '脚踝绷带': 'Ankle Taping',
  '冬季外场手套': 'Winter Player Gloves',
  '冬季佩戴': 'Winter Only',
  '随季节': 'Seasonal',
  '短袖': 'Short',
  '长袖': 'Long',
  '标准长度': 'Standard',
  '长袜': 'Long',
  '短袜': 'Short',
  '比赛环境与场地设定': 'Match Environment & Pitch Settings',
  '比赛时间': 'Time of Day',
  '每场随机': 'Random per Match',
  '白天': 'Day',
  '夜晚': 'Night',
  '比赛季节': 'Season',
  '夏季': 'Summer',
  '冬季': 'Winter',
  '比赛天气': 'Weather',
  '晴天': 'Fine',
  '雨天': 'Rain',
  '雪天': 'Snow',
  '球场条件': 'Pitch Condition',
  '干燥': 'Dry',
  '正常': 'Normal',
  '湿滑': 'Wet',
  '草皮长度': 'Grass Length',
  '短草': 'Short',
  '长草': 'Long',
  '时间、季节、天气与球场条件默认每场按赛程自动随机（夏季不出现雪天），也可手动指定；草皮长度默认标准并解锁选择。球场与比赛用球保持原样，修改环境设置保留当前赛程与积分。': 'Time, season, weather, and pitch condition randomize per match by default (no snow in summer) or can be fixed; grass length defaults to Standard and is unlocked. Stadium and ball stay untouched, and changing these keeps tournament progress.',
  '球衣穿法': 'Shirt Style',
  '袖长': 'Sleeve Length',
  '球袜长度': 'Sock Length',
  '护腕': 'Wrist Tape',

  // Positions
  '中锋': 'CF',
  '影锋': 'SS',
  '左边锋': 'LWF',
  '右边锋': 'RWF',
  '前腰': 'AMF',
  '左前卫': 'LMF',
  '右前卫': 'RMF',
  '中前卫': 'CMF',
  '后腰': 'DMF',
  '左后卫': 'LB',
  '右后卫': 'RB',
  '中后卫': 'CB',
  '门将': 'GK',
  '守门员': 'GK',

  // Attributes & Stat Groups
  '进攻': 'Attacking',
  '传球': 'Passing',
  '防守': 'Defending',
  '身体': 'Physical',
  '守门': 'Goalkeeping',
  '进攻意识': 'Offensive Awareness',
  '控球': 'Ball Control',
  '盘带': 'Dribbling',
  '紧密控球': 'Tight Possession',
  '地面传球': 'Low Pass',
  '空中传球': 'Lofted Pass',
  '射门': 'Finishing',
  '头球': 'Heading',
  '定位球': 'Place Kicking',
  '弧度': 'Curl',
  '防守意识': 'Defensive Awareness',
  '抢断': 'Tackling',
  '铲球': 'Tackling',
  '积极性': 'Aggression',
  '防守参与度': 'Defensive Engagement',
  '守门员意识': 'GK Awareness',
  '接球': 'GK Catching',
  '解围': 'GK Clearing',
  '扑救反应': 'GK Reflexes',
  '覆盖范围': 'GK Reach',
  '速度': 'Speed',
  '爆发力': 'Acceleration',
  '脚下力量': 'Kicking Power',
  '跳跃': 'Jumping',
  '身体接触': 'Physical Contact',
  '身体控制': 'Balance',
  '体力': 'Stamina',
  '总评': 'OVR',
  '基础值': 'Base',
  '加点': 'Points',
  '最终值': 'Final',
  '教练加成': 'Coach Boost',
  '增能加成': 'Booster',
  '全部属性': 'All Stats',
  '仅看关键属性': 'Key Stats Only',

  // Training & Skills
  '自动加点': 'Auto-Allocate',
  '按位置推荐加点': 'Auto by Position',
  '清空加点': 'Reset Points',
  '重置加点': 'Reset Points',
  '可用培养点': 'Available Points',
  '已用培养点': 'Used Points',
  '总培养点': 'Total Points',
  '培养详情': 'Progression Details',
  '前后对比': 'Before / After',
  '射门训练': 'Shooting',
  '传球训练': 'Passing',
  '盘带训练': 'Dribbling',
  '灵巧': 'Dexterity',
  '下肢力量': 'Lower Body Strength',
  '空中力量': 'Aerial Strength',
  '防守训练': 'Defending',
  '守门 1': 'GK 1',
  '守门 2': 'GK 2',
  '守门 3': 'GK 3',
  '天生技能': 'Innate Skills',
  '追加技能': 'Additional Skills',
  '特殊技能': 'Show Time Skills',
  'AI 比赛风格': 'AI Playing Styles',
  '增能槽': 'Booster Slots',
  '第一增能': 'Booster 1',
  '第二增能': 'Booster 2',
  '自定义增能': 'Custom Booster',
  '已选技能': 'Selected Skills',
  '搜索技能': 'Search skills',
  '技能库': 'Skill Library',
  '风格兼容检查': 'Style Compatibility',

  // Card, Model, Motion
  '外观与身体配置模板': 'Appearance & Body Template',
  '跟随原球员': 'Follow Source Player',
  '输入原版球员名或编号搜索（如 Messi / 编号）': 'Search native player name or ID (e.g. Messi / ID)',
  '搜索模板': 'Search Templates',
  '恢复跟随原球员': 'Reset to Source Player',
  '正在搜索外观模板…': 'Searching appearance templates…',
  '设为外观模板': 'Set as Template',
  '当前模板': 'Current Template',
  '含专属手臂纹身': 'Has Unique Arm Tattoo',
  '无纹身': 'No Tattoo',
  '没有找到带原版外观记录的球员，换个关键词试试。': 'No native players with appearance records found. Try another keyword.',
  '已恢复跟随原球员外观模板（当前体型与肤色保持不变）': 'Reset to source player template (physique & skin tone preserved)',
  '用于控制身体底层外观与手臂纹身来源。选错带纹身底板时，可搜一名不带纹身的原版球员直接替换；': 'Controls base body appearance & arm tattoo source. If you picked a tattooed donor by mistake, swap to a non-tattooed native player here; ',
  '已调好的 14 项体型、肤色、脸模、卡面与动作手感全部保留': 'all 14 physique sliders, skin tone, face model, card & motion are preserved',
  '原版卡面预设': 'Native Card Presets',
  '人物立绘': 'Player Portrait',
  '上传立绘': 'Upload Portrait',
  '上传图片': 'Upload Image',
  '移除图片': 'Remove Image',
  '头像缩略图': 'Avatar Thumbnail',
  '调整位置与缩放': 'Adjust Position & Scale',
  '缩放': 'Scale',
  '水平偏移': 'Horizontal Offset',
  '垂直偏移': 'Vertical Offset',
  '水平位置': 'X Position',
  '垂直位置': 'Y Position',
  '脸模来源': 'Face Model Source',
  '导入 GLB 模型': 'Import GLB Model',
  '恢复默认视角': 'Reset Camera',
  '重置视角': 'Reset Camera',
  '线框': 'Wireframe',
  '当前动作': 'Current Motion',
  '搜索动作来源球员': 'Search motion source player',
  '套用动作': 'Apply Motion',
  '恢复默认动作': 'Reset Motion',

  // My Players / Library / Packs / Coaches
  '搜索球员姓名、ID 或球队': 'Search by player name, ID, or club',
  '搜索球员': 'Search players',
  '分组管理': 'Folders',
  '全部分组': 'All Folders',
  '新建分组': 'New Folder',
  '重命名分组': 'Rename Folder',
  '删除分组': 'Delete Folder',
  '全选搜索结果': 'Select All Results',
  '取消全选': 'Deselect All',
  '加载编辑': 'Load to Edit',
  '复制为新球员': 'Clone as New Player',
  '删除球员': 'Delete Player',
  '上一页': 'Previous',
  '下一页': 'Next',
  '新建卡包': 'New Pack',
  '卡包名称': 'Pack Name',
  '卡包封面': 'Pack Cover',
  '卡池成员': 'Pack Pool',
  '添加球员': 'Add Player',
  '添加自建球员': 'Add Custom Player',
  '添加原版球员': 'Add Native Player',
  '抽取概率': 'Draw Rate',
  '权重': 'Weight',
  '保存卡包': 'Save Pack',
  '新建教练': 'New Coach',
  '保存教练': 'Save Coach',
  '教练姓名': 'Coach Name',
  '战术熟练度': 'Team Playstyle Proficiency',
  '控球游戏': 'Possession Game',
  '快速反击': 'Quick Counter',
  '长传反击': 'Long Ball Counter',
  '边路进攻': 'Out Wide',
  '长传球': 'Long Ball',
  '教练头像': 'Coach Portrait',

  // APK Build & Game Source
  '生成完整游戏 APK': 'Build Full Game APK',
  '生成球员增量小包 (.73pack)': 'Build Incremental Pack (.73pack)',
  '选择要写入的球员': 'Select Players to Include',
  '选择要写入的教练': 'Select Coaches to Include',
  '选择要写入的卡包': 'Select Packs to Include',
  '安装方式': 'Installation Mode',
  '覆盖安装': 'Update Install',
  '共存安装': 'Side-by-Side Install',
  '导入原版 APK': 'Import Base APK',
  '选择 APK 文件': 'Choose APK File',
  '重新扫描资源库': 'Rescan Library',
  '刷新资源库': 'Refresh Library',
  '任务中心': 'Task Center',
  '后台任务': 'Background Tasks',

  // Tools & Storage Manager
  '磁盘占用与存储管理': 'Disk Usage & Storage Manager',
  '刷新统计': 'Refresh Stats',
  '导出文件与缓存': 'Exports & Cache',
  'AI 组别进度': 'AI Division Progress',
  '实际磁盘占用': 'Actual Disk Usage',
  '硬链接去重节省': 'Saved by Hardlinks',
  '可安全释放': 'Safely Reclaimable',
  '历史完整 APK 保留数量': 'Full APKs to Keep',
  '完整 APK 导出成功后提醒清理旧 APK': 'Remind to clean old APKs after full APK export',
  '保存策略': 'Save Policy',
  '小包基准 APK 与当前资料库不受此上限影响，永远受保护。': 'Base APKs for packs and the active library are always protected.',
  '切换到同包名的最新基准': 'Switch to Latest Base',
  '选择同来源 APK': 'Pick Matching APK',
  '查看文件与原因': 'View files & reasons',
  '受保护': 'Protected',
  '无需清理': 'Clean',
  '预览清理': 'Preview Cleanup',
  '确认清理': 'Confirm Cleanup',
  '清除未保护导出文件': 'Clear Unprotected Exports',
  '确认删除未保护导出': 'Confirm Delete Exports',
  '检查本地 .73pack': 'Inspect Local .73pack',
  '清理编译缓存': 'Clear Compiler Cache',
  '查看清单': 'Inspect',
  '直接安装': 'Install Now',
  '打开所在位置': 'Open Folder',
  '暂无导出文件。': 'No exported files yet.',
  'AI 球队风格抽取': 'AI Team Style Rotation',
  '六风格洗牌轮换（每轮六种各一次）': '6-Style Shuffle Bag (each once per cycle)',
  '独立随机（允许连续重复）': 'Independent Random (repeats allowed)',
  '将这些 AI 组别设置写入下次生成的 APK': 'Write these AI Division settings into next APK build',
  '当前组别': 'Current Division',
  '积分 / 评分': 'Points / Rating',
  '本轮已赛场次': 'Matches Played',
  '胜场': 'Wins',
  '平场': 'Draws',
  '负场': 'Losses',
  '保存 AI 组别设置': 'Save AI Division Settings',
  '内容包清单': 'Pack Manifest',
  '目标游戏': 'Target Game',
  '包协议': 'Protocol',
  '基准程序 ID': 'Base Program ID',
  '创建时间': 'Created At',
  '旧协议(无版本号)': 'Legacy (no revision)',
  '未标注': 'Unspecified',
  '未记录': 'Not recorded',
  '选择要分享的自制球员': 'Select Custom Players to Share',
  '导出所选球员': 'Export Selected Players',
  '游戏设置': 'Game Settings',
  '保存设置': 'Save Settings',
  '已校验并切换资料库来源': 'Verified and switched library source',
  'AI 组别设置已保存': 'AI Division settings saved',
  '已保存；请在 APK 实装页生成游戏安装包。': 'Saved. Build the game APK on the Build APK page.',

  // Storage categories from backend
  '当前小包基准 APK': 'Active Pack Base APK',
  '当前资料库来源 APK': 'Active Library Source APK',
  '历史完整 APK': 'Older Full APKs',
  '历史已轮换基准 APK': 'Retired Base APKs',
  '当前有效增量小包 (.73pack)': 'Active Incremental Packs (.73pack)',
  '已过期增量小包': 'Expired Incremental Packs',
  '导出目录其他文件': 'Other Export Files',
  '编译与导出缓存': 'Compiler & Export Cache',
  '游戏资料库与提取资产': 'Game Library & Extracted Assets',
  '自建球员工程与资源': 'Custom Players & Projects',
  '签名密钥与配置': 'Signing Keys & Config',
};

const PATTERNS = [
  [/^已切换外观模板为\s*(.+?)（已保留当前体型、肤色、脸模与动作）$/, (_, n) => `Switched appearance template to ${n} (physique, skin tone, face & motion preserved)`],
  [/^(.+?)\s*·\s*#(\d+)\s*·\s*(含专属手臂纹身|无纹身)$/, (_, pos, id, tag) => `${pos} · #${id} · ${translateExact(tag)}`],
  [/^(.+?)\s*·\s*先读取球员$/, (_, a) => `${translateExact(a)} · Load a player first`],
  [/^共\s*(\d+)\s*个文件$/, (_, n) => `${n} file(s)`],
  [/^(\d+)\s*个文件$/, (_, n) => `${n} file(s)`],
  [/^可释放\s*(.+)$/, (_, s) => `Reclaimable ${s}`],
  [/^\(显示\s*(.+)\)$/, (_, s) => `(Apparent ${s})`],
  [/^组别\s*(\d+)$/, (_, n) => `Division ${n}`],
  [/^设计\s*v(\d+)$/, (_, n) => `Design v${n}`],
  [/^当前资料库来源：(.+)$/, (_, p) => `Current library source: ${p}`],
  [/^包含文件\s*\((\d+)\):$/, (_, n) => `Included files (${n}):`],
  [/^校验失败：(.+)$/, (_, e) => `Validation failed: ${e}`],
  [/^已保存保留策略：保留最近\s*(\d+)\s*个完整 APK$/, (_, n) => `Policy saved: keep latest ${n} full APK(s)`],
  [/^已打包\s*(\d+)\s*名球员$/, (_, n) => `Packaged ${n} player(s)`],
  [/^已清除\s*(\d+)\s*个导出文件（保留\s*(\d+)\s*个受保护文件）$/, (_, d, p) => `Cleared ${d} export(s) (${p} protected kept)`],
  [/^已清理编译缓存\s*(.+?)，下次导出自动重建$/, (_, s) => `Cleared compiler cache (${s}); will rebuild on next export`],
  [/^卡号\s*(.+?)\s*·\s*身份\s*(.+)$/, (_, c, id) => `Card ${c} · ID ${id}`],
  [/^第\s*(\d+)\s*步$/, (_, n) => `Step ${n}`],
  [/^(.+?)\s*·\s*(\d+)\s*个文件$/, (_, desc, n) => `${translateExact(desc)} · ${n} file(s)`],
  [/^(\d+(?:\.\d+)?\s*[KMGT]?B)\s*·\s*受保护$/, (_, s) => `${s} · Protected`],
];

const escapeRegex = text => text.replace(/[.*+?^$\{\}()|[\]\\]/g, '\\$&');
const templates = Object.entries(uiTemplates).map(([source, target]) => {
  const parts = source.split('⟦⟧');
  return [new RegExp('^' + parts.map(escapeRegex).join('([\\s\\S]*?)') + '$'), target];
}).sort((a, b) => b[0].source.length - a[0].source.length);

const styleDescriptions = Object.fromEntries(Object.values(styleRules).map(rule => [
  rule.description, rule.english_description,
]));
const labels = {...EXACT, ...gameTerms, ...styleDescriptions, ...uiEnglish};
const hasChinese = /[\u3400-\u9fff]/;
const boosterNames = {
  '攻门': 'Shooting',
  '自由球': 'Free-kick Taking',
  '空中战': 'Aerial',
  '带球': 'Ball-carrying',
  '防守': 'Defending',
  '守门': 'Goalkeeping',
  '前锋直觉': "Striker's Instinct",
  '体力无限': 'Hard Worker',
  '门神': 'Saving',
  '传中大师': 'Crossing',
  '球场幻想家': 'Fantasista',
};

function preserveWhitespace(source, value) {
  return source.match(/^\s*/)[0] + value + source.match(/\s*$/)[0];
}

function translateExact(str) {
  if (!str) return str;
  const trimmed = str.trim();
  if (!trimmed) return str;
  if (Object.hasOwn(labels, trimmed)) {
    return preserveWhitespace(str, labels[trimmed]);
  }
  if (!hasChinese.test(trimmed)) return str;
  for (const [pattern, target] of templates) {
    const match = trimmed.match(pattern);
    if (match) {
      return preserveWhitespace(str, target.replace(/\{(@?)(\d+)\}/g, (_, localize, index) => {
        const value = match[Number(index)];
        return localize ? translateExact(value) : value;
      }));
    }
  }
  for (const [re, fn] of PATTERNS) {
    const m = trimmed.match(re);
    if (m) {
      const out = fn(...m);
      return preserveWhitespace(str, out);
    }
  }
  for (const delimiter of [' · ', '、']) {
    if (!trimmed.includes(delimiter)) continue;
    const parts = trimmed.split(delimiter).map(translateExact);
    if (!parts.some(part => hasChinese.test(part))) {
      return preserveWhitespace(str, parts.join(delimiter === '、' ? ', ' : delimiter));
    }
  }
  const booster = trimmed.match(/^(.+?) (\+[0-9]+)$/);
  if (booster) {
    const [_, name, boost] = booster;
    const label = boosterNames[name] ?? labels[name];
    if (label) return preserveWhitespace(str, label + ' ' + boost);
  }
  return str;
}

const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'CODE', 'PRE', 'TEXTAREA']);
const optionValues = new WeakMap();

function shouldSkip(node) {
  let el = node.nodeType === 1 ? node : node.parentElement;
  while (el) {
    if (SKIP_TAGS.has(el.tagName) || el.hasAttribute('data-no-i18n')) return true;
    el = el.parentElement;
  }
  return false;
}

function applyTextNode(node, lang) {
  if (shouldSkip(node)) return;
  const val = node.nodeValue;
  if (!val || !val.trim()) return;
  const saved = origText.get(node);
  const original = saved?.translated === val ? saved.original : val;
  const english = node.parentElement?.getAttribute('data-i18n-en');
  const translated = lang === 'en' ? (english ?? translateExact(original)) : original;
  const option = node.parentElement?.closest('option');
  if (lang === 'en' && translated !== original) {
    origText.set(node, {original, translated});
    // An option without a value submits its text. Keep the original value for the app.
    if (option && !option.hasAttribute('value')) {
      const value = option.value;
      optionValues.set(option, value);
      option.value = value;
    }
  } else {
    origText.delete(node);
  }
  if (val !== translated) node.nodeValue = translated;
  if (lang === 'zh' && optionValues.has(option)) {
    if (option.value === optionValues.get(option)) option.removeAttribute('value');
    optionValues.delete(option);
  }
}

function applyElementAttrs(el, lang) {
  if (el.nodeType !== 1 || shouldSkip(el)) return;
  const saved = origAttrs.get(el) ?? {};
  for (const attr of ATTRS) {
    const current = el.getAttribute(attr);
    const original = saved[attr]?.translated === current ? saved[attr].original : current;
    const translated = lang === 'en' ? translateExact(original) : original;
    if (lang === 'en' && translated !== original) {
      saved[attr] = {original, translated};
    } else {
      delete saved[attr];
    }
    if (current !== translated) el.setAttribute(attr, translated);
  }
  if (Object.keys(saved).length) origAttrs.set(el, saved);
  else origAttrs.delete(el);
}

function walkTree(rootNode, lang) {
  if (!rootNode) return;
  if (rootNode.nodeType === 3) {
    applyTextNode(rootNode, lang);
    return;
  }
  if (rootNode.nodeType === 1) {
    applyElementAttrs(rootNode, lang);
  }
  const walker = document.createTreeWalker(rootNode, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let cur;
  while ((cur = walker.nextNode())) {
    if (cur.nodeType === 3) applyTextNode(cur, lang);
    else if (cur.nodeType === 1) applyElementAttrs(cur, lang);
  }
}

let currentLang = 'zh';
let observer = null;
let rafQueued = false;
const pendingNodes = new Set();

function flushPending() {
  rafQueued = false;
  if (currentLang !== 'en') {
    pendingNodes.clear();
    return;
  }
  const list = [...pendingNodes];
  pendingNodes.clear();
  for (const node of list) {
    if (node.isConnected) walkTree(node, 'en');
  }
}

function ensureObserver() {
  if (observer) return;
  observer = new MutationObserver(mutations => {
    if (currentLang !== 'en') return;
    for (const m of mutations) {
      if (m.type === 'childList') {
        for (const n of m.addedNodes) pendingNodes.add(n);
      } else {
        pendingNodes.add(m.target);
      }
    }
    if (pendingNodes.size && !rafQueued) {
      rafQueued = true;
      requestAnimationFrame(flushPending);
    }
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ATTRS,
  });
}

function stopObserver() {
  if (observer) {
    observer.disconnect();
    observer = null;
  }
  pendingNodes.clear();
}

export function getLang() {
  return currentLang;
}

export function t(zhText, lang = currentLang) {
  return lang === 'en' ? translateExact(zhText) : zhText;
}

export function setLang(next, { persist = true } = {}) {
  const lang = next === 'en' ? 'en' : 'zh';
  const changed = lang !== currentLang;
  currentLang = lang;
  document.documentElement.dataset.lang = lang;
  document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN';
  document.title = lang === 'en' ? 'Player Studio' : '球员工坊';
  if (persist) {
    try { localStorage.setItem(STORAGE_KEY, lang); } catch {}
  }
  if (lang === 'en') {
    walkTree(document.body, 'en');
    ensureObserver();
  } else {
    stopObserver();
    if (changed) walkTree(document.body, 'zh');
  }
  window.dispatchEvent(new CustomEvent('studio-lang-change', { detail: { lang } }));
  return lang;
}

export function initI18n() {
  const urlLang = new URLSearchParams(location.search).get('lang');
  let stored = null;
  try { stored = localStorage.getItem(STORAGE_KEY); } catch {}
  const initial = (urlLang === 'en' || urlLang === 'zh') ? urlLang : (stored === 'en' ? 'en' : 'zh');
  currentLang = initial;
  document.documentElement.dataset.lang = initial;
  document.documentElement.lang = initial === 'en' ? 'en' : 'zh-CN';
  if (initial === 'en') {
    document.title = 'Player Studio';
    walkTree(document.body, 'en');
    ensureObserver();
  }
  window.__studioI18n = { getLang, setLang, t };
  return initial;
}
