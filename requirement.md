# 需求：CSV 数据透视工具
中等项目 200-500 行。3-4 个文件：
- pivot.py: 主程序，读 CSV，pivot 操作
- parser.py: CSV 解析（支持引号、转义）
- stats.py: 数值统计（sum/avg/count/min/max）
- test_*.py: 单元测试覆盖每个模块
要求：完整错误处理（文件不存在、列缺失、类型错误），需要 pytest 单元测试。
