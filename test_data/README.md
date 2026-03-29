# 数据文件说明

默认运行：

```bash
python main.py
```

默认会读取：

- `test_data/sample_input.json`
- `test_data/optimize_showcase.json` 可用于演示“优化优于初始贪心”的效果：
  `python main.py --mode optimize --input test_data/optimize_showcase.json`

你以后替换自己数据时，推荐复制：

- `test_data/input_template.json`

字段说明：

- `config`: 可选的算法参数；不改也可以，保留默认值即可。
- `buildings`: 教学楼列表。
- `buildings[].rooms`: 该楼下的教室列表，容量必须是 `50`、`100`、`200` 之一。
- `courses`: 课程列表，每门课需要 `id`、`stu_num`、`time_slot`。
- `student_groups`: 学生组列表。
- `student_groups[].schedule`: 该组的课表，程序会自动根据这里的 `course_id` 把学生组关联到对应课程。
- `dist_building`: 教学楼之间的距离矩阵。
- `dist_dorm`: 宿舍到教学楼的距离矩阵。

注意：

- `time_slot` 必须在 `0` 到 `34` 之间。
- 距离必须完整且非负。
- `course_id`、`room_id`、`building_id` 不能重复。
- `student_groups[].schedule` 中引用的课程必须已经出现在 `courses` 里。
