# Smart Scheduler

一个面向教室分配场景的排课优化项目。

预期的应用场景是面对已经完成学生课表制定和上课时间安排之后，为课程分配合适教室，并尽量降低学生移动成本、容量违约成本和房间冲突成本。

项目采用两阶段求解流程：

1. 使用贪心策略快速构造一个可用初始解。
2. 在初始解基础上做局部搜索与模拟退火优化，持续改进总成本。

## 核心能力

- 支持从 JSON 文件加载教学楼、教室、课程、学生组和距离矩阵。
- 支持 `greedy` 与 `optimize` 两种运行模式。后者为核心优化功能
- 目标函数统一纳入首课通勤、连续课程换楼和约束惩罚。
- 支持通过配置项调整贪心与优化阶段参数。
- 提供固定样例数据与独立测试数据生成器，方便演示、回归测试和压力测试。

## 目录结构

```text
smart_scheduler_ws/
├── README.md
├── main.py
├── scheduler.py
├── greedy.py
├── core.py
├── models.py
├── config.py
├── constants.py
├── shared_types.py
├── optimizer/
├── tests/
├── test_data/
├── test_case_generator/
└── docs/
```

各目录职责简述：

- `main.py`：命令行入口，负责读取 JSON、构建调度器并执行求解。
- `scheduler.py`：调度器主装配层，串联贪心和优化流程。
- `greedy.py`：初始解生成逻辑。
- `core.py`：状态管理、距离访问与目标函数评估。
- `optimizer/`：局部搜索、状态更新、接受准则等优化模块。
- `tests/`：核心行为与回归测试。
- `test_data/`：固定样例输入与模板数据。
- `test_case_generator/`：独立测试数据生成器，可按规模自动生成 JSON。
- `docs/`：设计思路、实现说明与答辩材料。

## 快速开始

### 1. 环境准备

建议使用 Python 3.10 及以上版本，并在仓库根目录执行命令。

### 2. 运行默认样例

默认会读取 `test_data/sample_input.json`：

```bash
python main.py
```

### 3. 运行优化模式

```bash
python main.py --mode optimize --input test_data/optimize_showcase.json --max-iters 200
```

常用参数：

- `--input`：指定输入 JSON 文件。
- `--mode`：`greedy` 或 `optimize`。
- `--max-iters`：优化最大迭代次数。
- `--initial-temp`：优化初始温度。
- `--seed`：优化随机种子。
- `--verify`：优化模式下开启周期性校验。

## 输入数据格式

程序读取的根对象字段如下：

- `config`：可选配置项。
- `buildings`：教学楼列表。
- `courses`：课程列表，包含 `id`、`stu_num`、`time_slot`。
- `student_groups`：学生组列表，包含 `weight`、`dorm_id` 和 `schedule`。
- `dist_building`：教学楼之间的距离矩阵。
- `dist_dorm`：宿舍到教学楼的距离矩阵。

说明：

- `student_groups[].schedule` 中的 `course_id` 必须能在 `courses` 中找到。
- `time_slot` 当前应落在项目支持的时间槽范围内。
- 距离矩阵需要完整且非负。
- 固定样例与模板可参考 [test_data/README.md](test_data/README.md)。

## 测试与验证

运行核心测试：

```bash
python -m unittest discover -s tests -v
```

如果你的环境已经安装 `pytest`，也可以直接运行：

```bash
pytest
```

当前测试主要覆盖：

- JSON 输入解析与异常处理。
- 贪心求解与优化行为。
- 固定模板输入的可加载性。
- 关键状态与约束不变量。

## 测试数据生成器

仓库内置了独立的测试数据生成器，可生成能被 `main.py` 直接读取的 JSON 文件。

生成一份默认规模数据：

```bash
python -m test_case_generator
```

生成后可直接运行：

```bash
python main.py --input .\test_case_generator\generated\<your_case>.json
python main.py --mode optimize --input .\test_case_generator\generated\<your_case>.json
```

更多参数与示例见 [test_case_generator/README.md](test_case_generator/README.md)。

## 输出结果

程序默认输出两类结果：

- 调度报告，如已分配课程数、总成本、惩罚成本、终止原因等。
- `course_id -> building_id, room_id` 的课程-教学楼-教室映射。

这使它既可以用于命令行演示，也方便后续接入可视化界面或评测脚本。

## 算法概览

- 贪心阶段优先快速构造可用解，减少未分配课程。
- 优化阶段围绕统一目标函数执行局部调整。
- 目标函数由“距离成本 + 惩罚成本”组成。
- 距离成本同时考虑宿舍到首课、连续课程之间的换楼距离。
- 惩罚成本主要覆盖容量超限和同时间槽房间冲突。

## 优化方向

- 项目仅解决“校园道路交通压力过大”既定问题，没有考虑同一栋教学楼内的空间结构，后续可以为教室增加楼层属性，进一步降低学生爬楼的负担
- 评估排课方案时仅考虑了学生移动成本，后续可以将授课教师移动成本也加入评估函数，参考波士顿大学：https://github.com/zilongpa/BU-Classroom-Assignment-Optimization
- 项目基于已经完成课程时间、上课人数等工作之后的课表，专注于课程对教室的匹配工作，如果需要性能更强的排课器，后续增加对于安排课程时间和上课人员的支持，理论上能进一步压缩移动负担
- 部分课程或教师可能对于某种类型的教室存在偏好（如插座数量，座椅是否可移动），后续可以在教室和课程定义中追加sp_feature向量，优先匹配偏好教室并提高后续优化器破坏“偏好配对”所需的成本
