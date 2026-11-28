"""Unit tests build the gateway without M12; the cluster manifest requires the rail (default on)."""

import os

os.environ.setdefault("REQUIRE_INPUT_RAIL", "false")
