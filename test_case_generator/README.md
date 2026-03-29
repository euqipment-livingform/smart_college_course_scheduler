# Test Case Generator

这个子项目是独立于现有排课器的测试数据生成器，目标只有一件事：

- 接受你手动指定的测试规模
- 自动生成当前 `smart_scheduler_ws/main.py` 可以直接读取的 JSON 样例
- 把所有相关代码和默认输出都限制在本子文件夹内，避免污染现有目录

## 设计上对 `docs/chat.md` 的补强

在保留原大纲主线的基础上，这个实现额外补了几件会直接影响“能不能喂给现有程序”的关键点：

- 输出 schema 严格对齐当前调度器真实读取字段，只保留 `buildings / courses / student_groups / dist_building / dist_dorm / config`
- 不依赖 `scipy`，时间槽可行性校验改为标准库 DFS 增广路匹配
- 不再允许“生成后再丢弃零人数课程”，而是强制每门课至少绑定一个学生组，保证最终课程数与用户输入一致
- 在生成前先做参数可行性检查，提前拦截 `课程数 > 房间数 * 时间槽数` 这类结构性无解配置
- 默认输出到当前子项目的 `generated/` 目录，不会把新文件散落到仓库别处

## 用法

在 `smart_scheduler_ws` 目录下执行：

```bash
python -m test_case_generator
```

默认会在：

```text
test_case_generator/generated/
```

里生成一个 JSON 文件。

指定规模示例：

```bash
python -m test_case_generator ^
  --num-buildings 5 ^
  --num-dorms 3 ^
  --num-courses 150 ^
  --num-groups 300 ^
  --rooms-per-building 4 8 ^
  --used-time-slots 18 24 ^
  --group-course-count 2 5
```

指定输出路径示例：

```bash
python -m test_case_generator --output .\test_case_generator\generated\case_150_300.json
```

生成后可直接交给现有排课器：

```bash
python main.py --input .\test_case_generator\generated\case_150_300.json
python main.py --mode optimize --input .\test_case_generator\generated\case_150_300.json
```

## 支持参数

- `--num-buildings`: 教学楼数量
- `--num-dorms`: 宿舍数量
- `--num-courses`: 课程数量
- `--num-groups`: 学生组数量
- `--rooms-per-building MIN MAX`: 每栋楼教室数范围
- `--used-time-slots MIN MAX`: 实际启用的时间槽范围
- `--group-course-count MIN MAX`: 每个学生组选课数量范围
- `--group-weight MIN MAX`: 学生组权重范围
- `--groups-per-dorm MIN MAX`: 每个宿舍容纳的学生组数量范围
- `--scenario`: `balanced`、`tight`、`optimize_showcase`
- `--seed`: 随机种子
- `--output`: 指定输出路径
- `--compact`: 输出紧凑 JSON

## 目录结构

```text
test_case_generator/
├── __init__.py
├── __main__.py
├── builders.py
├── config.py
├── exceptions.py
├── exporter.py
├── main.py
├── models.py
├── validators.py
├── README.md
└── tests/
```
