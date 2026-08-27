# Windows 装块烂了三个版本没人发现

1. **发生了什么**：install.bat 停在 v1.2.x 状态——1.5.0 已删除的 /tc 还在装、装的是
   不存在的目录，audit/assurance 从未装进 Windows。Windows 用户从 1.4.0 起就装不到
   新 skill，全程零报错。
2. **为什么没被拦住**：check_skills.py 门禁只查 install.sh；install.bat 不在任何
   判据的扫描面里。「装机脚本」和「检查器」分家，检查器看不见另一台床。
3. **防复发**：check_skills.py 已扩成同时校验 install.sh 与 install.bat 的装块
   （双注入验红过）。教训入账：**每个分发面都要有判据盯着，少一个面就瞎一个平台**。
