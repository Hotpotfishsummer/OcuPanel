#!/bin/bash
# 双击即可启动（macOS Finder 里双击这个文件会打开「终端」并运行下面的命令）
# 等价于：bash start.sh
cd "$(dirname "$0")" || exit 1
bash start.sh "$@"
status=$?

# 双击运行时不要把窗口一闪而过，停住让人看清结果
if [[ -t 0 && $# -eq 0 ]]; then
  echo
  read -r -n 1 -p "按任意键关闭窗口…" _
  echo
fi
exit "$status"
