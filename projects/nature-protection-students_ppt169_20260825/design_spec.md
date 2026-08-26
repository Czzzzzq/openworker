<!-- ppt-master-schema: design-spec/v1 -->
# Nature Protection for University Students - Design Spec

## I. Project Information

| Item | Value |
| --- | --- |
| Project Name | Nature Protection for University Students |
| Canvas Format | PPT 16:9 (1280×720) |
| Page Count | 13 |
| Primary Language | zh-CN |
| Target Audience | 大学生，课堂、社团活动与环保主题讨论参与者 |
| Communication Intent | 先建立自然保护与校园日常相关的认知，再用数据解释紧迫性，最后推动一个具体行动。 |
| Desired Audience Outcome | 能说出一个问题、理解个人与校园选择的关系，并愿意采取一个可执行行动。 |
| Core Message / Ask / Action | 保护自然从校园开始：减量、循环、修复、倡议，从下一次选择做起。 |
| Delivery Context | 现场演讲为主，课后可独立阅读 |
| Artifact Afterlife | 课堂分享、社团活动、环保讨论通用材料 |
| Reading Mode | balanced |
| Content Strategy | 允许围绕大学生行动重新组织和简化研究事实，不新增未经来源支持的数字。 |
| Design Style | 活泼环保信息图：圆角卡片、手绘感线条、几何形状与清晰数据图表 |
| AI Image Acquisition Path | not applicable; editable SVG shapes and charts only |
| Generation Mode | continuous |
| Spec Refinement | disabled |
| Speaker Notes | disabled — user did not request notes |
| Custom Animations | disabled — user did not request animation |
| Narration Audio | disabled — user did not request narration |
| Created Date | 2026-08-25 |

## II. Canvas Specification

| Property | Value |
| --- | --- |
| Format | PPT 16:9 |
| Dimensions | 1280 × 720 |
| viewBox | `0 0 1280 720` |
| Margins | 56 px safe margin |
| Content Area | x=56..1224, y=44..676 |

## III. Visual Theme

### Theme Style
- **Mode**: custom
- **Visual style**: custom
- **Theme**: lively campus ecology infographic
- **Tone**: optimistic, practical, energetic, non-judgmental

### Color Scheme
| Role | HEX | Purpose |
| --- | --- | --- |
| Background | #FFF8EA | warm paper-like field |
| Secondary background | #E9F5E7 | soft content panels |
| Primary | #195B45 | headings and key data |
| Accent | #F47C5A | urgency and calls to action |
| Secondary accent | #2D9CDB | water/connection cues |
| Body text | #20352D | readable body copy |

## IV. Typography System

### Font Plan
| Role | Character (Reference) | Primary | English if non-English | Fallback tail |
| --- | --- | --- | --- | --- |
| Title | friendly bold sans | Microsoft YaHei | Arial | sans-serif |
| Body | neutral readable sans | Microsoft YaHei | Arial | sans-serif |
| Data | compact numeric emphasis | Arial | Arial | sans-serif |

- **Title stack**: Microsoft YaHei, Arial, sans-serif
- **Body stack**: Microsoft YaHei, Arial, sans-serif
- **Data stack**: Arial, sans-serif

### Font Size Hierarchy
| Purpose | Anchor Size (px) |
| --- | ---: |
| Body | 24 |
| Title | 44 |
| Subtitle | 30 |
| Annotation | 16 |
| Caption | 19 |
| Card label | 20 |
| Data | 34 |

## V. Layout Principles

### Deck-wide Direction
- **Hierarchy direction**: title → one dominant visual/data statement → short explanation → action cue.
- **Composition tendency**: rounded cards and organic circles provide a flexible grid; charts occupy the visual center and remain editable.
- **Cross-page continuity**: recurring green top label, coral action marker, and small leaf-dot motif; each section gets a distinct accent.
- **Spacing posture**: open on cover and conclusion; balanced on evidence pages; dense only on action matrix.

## VI. Icon Usage Specification

- **Primary bundled library**: tabler-filled

| Icon Path | Suitable Scenarios |
| --- | --- |
| tabler-filled/leaf | nature, restoration, campus green |
| tabler-filled/recycle | circular use, reuse |
| tabler-filled/droplet | water ecosystem |
| tabler-filled/plant | repair and biodiversity |
| tabler-filled/users | collective action |
| tabler-filled/arrow-right | action sequence |

## VII. Visualization Reference List

| Page | Family | Template | Usage |
| --- | --- | --- | --- |
| P04 | chart | horizontal_bar_chart | compare annual plastic production and aquatic leakage |
| P09 | chart | donut_chart | show the 30% restoration target as a bounded goal |
| P11 | table | comparison_matrix | map individual actions to collective campus rules |

## VIII. Image Resource List

| Filename | Dimensions | Ratio | Purpose | Type | Layout pattern | Crop Policy | Acquire Via | Status | Reference | text_policy | page_role |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## IX. Content Outline

### Part 1: 看见问题

#### Slide 01 - 保护自然，从校园开始
- **Audience move**: 从“这是宏大议题”转向“我与它有关”。
- **Layout**: 开放式封面，中央标题，四周有叶片、圆点和校园路径的轻量装饰。
- **Title**: 保护自然，从校园开始
- **Core message**: 下一次选择，就是行动的起点。
- **Content**: 副标题“大学生可参与的四类行动”；署名“环保主题分享”。

#### Slide 02 - 自然不是远方
- **Audience move**: 从旁观者转为校园生态系统的一员。
- **Layout**: 中央校园节点，连到水、土壤、空气、生物多样性四个节点。
- **Title**: 自然不是远方
- **Core message**: 校园里的消费、出行和空间使用都在影响自然。
- **Content**: 校园也是生态系统；每一次选择都连接着资源与环境。

#### Slide 03 - 四类行动地图
- **Audience move**: 从模糊的“环保”转向四类可理解的行动。
- **Layout**: 四张圆角彩色卡片，形成从个人到集体的行动地图。
- **Title**: 四类行动地图
- **Core message**: 保护自然可以从减量、循环、修复、倡议开始。
- **Content**: 减量：少一次性；循环：多使用一次；修复：让空间更有生命；倡议：把好选择变成共同规则。

### Part 2: 用数据理解紧迫性

#### Slide 04 - 塑料问题有多大？
- **Audience move**: 从“塑料很多”转向理解生产与泄漏的量级差异。
- **Layout**: 左侧大标题，右侧水平柱状对比图，底部保留来源脚注。
- **Title**: 塑料问题有多大？
- **Core message**: 塑料的规模很大，进入水生态系统的泄漏也不可忽视。
- **Content**: 人类每年生产超过4亿吨塑料；每年约1900–2300万吨塑料废弃物泄漏进入水生生态系统。事实编号：F001、F002。图表：horizontal_bar_chart；Native-ready=yes。

#### Slide 05 - 一次性用品，如何变成选择？
- **Audience move**: 从被动消费转向看见可改变的环节。
- **Layout**: 四步循环流程：购买 → 使用 → 丢弃 → 改变；最后回到购买。
- **Title**: 一次性用品，如何变成选择？
- **Core message**: 改变不只发生在“扔掉”那一刻，而是发生在选择之前。
- **Content**: 自带杯/餐具；优先耐用品；正确分类；和同伴一起改变默认选项。

#### Slide 06 - 每天被浪费的，不只是食物
- **Audience move**: 从“吃不完很正常”转向理解食物浪费的全球尺度。
- **Layout**: 超大数字“10亿+”，旁边用餐盘与地球的简图做语义连接。
- **Title**: 每天被浪费的，不只是食物
- **Core message**: 2022年全球家庭每天浪费超过10亿份餐食，选择从餐桌开始。
- **Content**: “超过10亿份餐食 / 每天 / 全球家庭”；下方注明“2022年，UNEP Food Waste Index 2024”。事实编号：F003。

#### Slide 07 - 从“吃多少”到“点多少”
- **Audience move**: 从震惊数据转向可执行的餐桌动作。
- **Layout**: 五张清单卡片，按“点餐—取餐—保存—分享—反馈”排列。
- **Title**: 从“吃多少”到“点多少”
- **Core message**: 减少浪费不靠完美，而靠下一次更准确的选择。
- **Content**: 先估量再取餐；小份优先；剩余打包；不随意多拿；把建议反馈给食堂。

### Part 3: 从理解到行动

#### Slide 08 - 气候与自然，是一张网
- **Audience move**: 从单点问题转向系统理解。
- **Layout**: 中央“自然”节点，连接气候、水、土壤、生物多样性四个节点。
- **Title**: 气候与自然，是一张网
- **Core message**: 环境问题相互连接，所以行动也需要个人、组织与制度共同参与。
- **Content**: IPCC AR6综合报告覆盖气候影响、风险、减缓与适应路径。事实编号：F005。

#### Slide 09 - 30%：一个需要共同完成的目标
- **Audience move**: 从“保护自然很抽象”转向理解可共同追踪的目标。
- **Layout**: 环形目标图显示30%，右侧解释恢复退化生态系统的含义。
- **Title**: 30%：一个需要共同完成的目标
- **Core message**: 到2030年，全球生物多样性框架提出至少恢复30%的退化生态系统。
- **Content**: “30% / 到2030年 / 恢复退化生态系统”；事实编号：F006。图表：donut_chart；Native-ready=yes。

#### Slide 10 - 今天就能做的 5 个动作
- **Audience move**: 从理解问题转向选择一个个人行动。
- **Layout**: 纵向编号 01–05，配合小图标与短句。
- **Title**: 今天就能做的 5 个动作
- **Core message**: 低门槛、可记录、能重复的行动最容易持续。
- **Content**: 自带水杯；少一次性包装；按需取餐；修复/再利用物品；观察并保护校园生境。

#### Slide 11 - 把个人选择，变成校园规则
- **Audience move**: 从“我来做”转向“我们一起改变默认选项”。
- **Layout**: 对比矩阵：个人动作 × 班级/社团机制，底部突出“从一次活动变成长期习惯”。
- **Title**: 把个人选择，变成校园规则
- **Core message**: 集体规则能让好选择更容易发生。
- **Content**: 自带杯 → 活动不提供一次性杯；分类 → 设置清晰回收点；修复 → 交换/维修日；倡议 → 每周公开记录。图表：comparison_matrix；Native-ready=yes。场景建议，不是外部统计。

#### Slide 12 - 7 天校园挑战
- **Audience move**: 从认同转向承诺与同伴参与。
- **Layout**: 7天横向时间线，节点为“选择—记录—邀请—复盘”。
- **Title**: 7 天校园挑战
- **Core message**: 选择一个动作，坚持七天，再邀请一位同伴。
- **Content**: Day 1选择；Day 2–6记录；Day 7复盘与分享；行动卡问题“我改变了什么默认选项？”

#### Slide 13 - 从下一次选择开始
- **Audience move**: 从听完演示转为带走一个具体承诺。
- **Layout**: 留白结尾，中央承诺句，四角回收四类行动词。
- **Title**: 从下一次选择开始
- **Core message**: 保护自然不是完美主义，而是从下一次选择开始。
- **Content**: “我今天选择：减量 / 循环 / 修复 / 倡议”；“把一个好选择，变成更多人的默认选择”。

## X. Speaker Notes Requirements

- **Generation**: disabled

## Quality Requirements

- All visible text, data labels, chart elements, and decorative shapes are represented in SVG pages.
- External claims use fact IDs in the outline and source footers on the relevant pages.
- No invented campus percentages or outcome statistics.
- Keep body text readable at 24 px anchor and maintain strong contrast.
