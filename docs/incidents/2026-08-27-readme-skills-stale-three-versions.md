# README 技能章节停更三个版本

1. **发生了什么**：v1.2.4（2026-03-22）之后 README/README_CN 未随 skill 演进更新。
   v1.6.0 收拢 skill 时发现：已删除的 /tc 仍被当卖点宣传六处，/exp 指向不存在的命令，
   新增的 /assurance /retire-checks 零提及，版本历史缺 1.3–1.5 整段。对外文档与产品
   貌合神离了五个月。
2. **为什么没被拦住**：check_skills.py 的交叉引用检查只扫 skills/ 目录——README 不在
   扫描面内。工具层当天对齐、散文层无人同步，与「多份执行器漏改一份」同类。
3. **防复发**：发版清单（OPENSOURCE_CHECKLIST.md）新增一条「版本表加行时 skill 章节
   必须同改」。目前没有机器判据守着 README 与 skills/ 的一致性——这条债在此明写，
   不假装闭环。
