#!/usr/bin/env bash
# 以 SDK 模式启动 dsh，并叠加 muse 覆盖层。Go 后端通过 stdio 与它通信，正常情况下不需要手动运行。
# 手动调试：DEEPSEEK_API_KEY=... ./dsh/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export DSH_HOME="${DSH_HOME:-$PWD/.dsh-home}"
export MUSE_SKILL_DIR="${MUSE_SKILL_DIR:-$PWD/dsh/skills}"
exec npx --yes @deepseek-ai/dsh --profile sdk --patch "$PWD/dsh/muse.patch.yml" "$@"
