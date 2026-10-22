"""Запуск окна двойным кликом из проводника.

Расширение .pyw связано с C:\\Windows\\pyw.exe - это лаунчер Python, который
работает без консоли и сам находит установленный интерпретатор, поэтому путь к
нему здесь нигде не зашит.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui import main     # noqa: E402

main()
