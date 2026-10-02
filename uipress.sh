#!/bin/bash
# 用法: uipress.sh X Y  —— 激活 cicada + AX 命中 + AXPress
cd ~/diepu-export
./.venv/bin/python ax.py press "$1" "$2" 2>&1 | sed 's/Position=.*//;s/Size=.*//' | head -3
