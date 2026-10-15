# 提交材料冻结清单（截止 2026-10-15 12:00）

## 官方命名（打包前必填）

- [ ] 组别：________
- [ ] 参赛编号：________
- [ ] 队伍名称：________
- [ ] 队长姓名：________

打包命令（四项填好后执行）：

```bash
python scripts/freeze_package.py --group 组别 --team 队伍名称 --captain 队长姓名 [--entry 参赛编号]
# 输出：dist_submit/组别-参赛编号-队伍名称-队长姓名.zip
```

## 包内清单（脚本自动收集）

1. **AGH 执行记录**：`evidence/run_*/`（bundle.json + agh_session.jsonl 会话原文 + checkpoint + audit）
2. **演示视频**：`demo_video.mp4`（人工录制后放入仓库根；分镜见 README"演示分镜"，5 分钟一镜到底）
3. **验证材料**：`evidence/checklist.md`（四阶段对照 + Agnes 四字段）+ `evidence/manual_check.md`
4. **本仓库 README.md**
5. 运行时资产：`phmlite/`、`dashboard/dist/`、`agent/skills/`、`.agh/skills/`、`scripts/`（评委复跑用）

## 脚本强制隔离（防泄露）

打包脚本显式排除：`.env`（Agnes Key）、`vendor/`（运行时便携包）、`docs/`（内部计划）、`data/*.db`、`.superpowers/`。
排除项为硬编码，不随参数变化。

## 公开/开源前最后检查（不可跳过）

- [ ] **删除 `.env` 中 `AGNES_API_KEY`，并在 Agnes 后台轮换该密钥**（README 已双重标注）；
- [ ] `docs/` 内部计划公开前评估处理；
- [ ] 换机器演示前重读 `scripts/setup_agh.md`（ACL 修复三步 + Key 注入 + 端口）。
