"""Run unit tests with network sockets blocked and .env loading disabled.

Usage: python3 run_tests_offline.py [test_nh_bond_parser test_main.BondMarketTests]
Tests use mocks and temporary directories; this does not run the briefing command.
"""

import os
import socket
import sys
import unittest
from unittest.mock import patch

import main


def run():
    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromNames(sys.argv[1:]) if sys.argv[1:] else loader.discover(".", pattern="test_*.py")
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Test network disabled")), \
         patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Test network disabled")), \
         patch.object(main, "load_dotenv"), patch.dict(os.environ, {}, clear=True):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(run())
