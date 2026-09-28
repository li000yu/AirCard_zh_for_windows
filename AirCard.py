#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AirCard for Windows - 单文件绿色版入口。

双击本文件即可运行 GUI；命令行亦可：
    python AirCard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aircard.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
