# 中银回撤测算站点

源码恢复自线上部署 `dep_97a51ca8611b43d790619cadb2174d54`（artifact-recovery）。

数据请求使用同源登录 Cookie；SDK 和客户端脚本查询版本更新为 `20260908-1`，避免旧缓存继续使用不携带凭据的逻辑。

容器代码更新后，还需重新发布此目录以使站点脚本修复生效：

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py deploy --slug china-bank-drawdown --title "中国银行回撤样本与价格参考" --source-dir sites/china-bank-drawdown --publish-dir sites/china-bank-drawdown --entrypoint index.html
```

Runtime seed 只用于初始化尚不存在的文档，重新部署不覆盖已保存的数据。
