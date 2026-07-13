# Copyright © 2025, Arm Limited and Contributors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import yaml

from .config import WORKSPACE_DIR


LOG_FILE_NAME = "invocation_reasons.yaml"
MCP_TRAFFIC_LOG_ENV = "MCP_LOG_FILE"
MCP_TRAFFIC_LOG_DEFAULT = "/workspace/mcp-traffic.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_invocation_reason(
    tool: str,
    reason: Optional[str],
    args: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Append a YAML document with the tool invocation reason and metadata to /workspace/invocation_reasons.yaml.
    Also append a JSONL call entry to MCP_LOG_FILE.

    Returns the entry ID so the caller can pair the tool result with this invocation.
    Errors are swallowed to avoid impacting tool execution.
    """
    entry_id = str(uuid.uuid4())
    timestamp = _now_iso()

    if reason:
        entry = {
            "id": entry_id,
            "timestamp": timestamp,
            "tool": tool,
            "args": args or {},
            "reason": str(reason),
        }

        log_path = os.path.join(WORKSPACE_DIR, LOG_FILE_NAME)

        try:
            os.makedirs(WORKSPACE_DIR, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                yaml.safe_dump(entry, f, explicit_start=True, sort_keys=False, allow_unicode=True)
        except Exception:
            pass

    traffic_entry = {
        "id": entry_id,
        "timestamp": timestamp,
        "tool": tool,
        "args": args or {},
        "invocation_reason": reason,
    }
    traffic_path = os.environ.get(MCP_TRAFFIC_LOG_ENV, MCP_TRAFFIC_LOG_DEFAULT)
    try:
        os.makedirs(os.path.dirname(traffic_path) or WORKSPACE_DIR, exist_ok=True)
        with open(traffic_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(traffic_entry) + "\n")
    except Exception:
        pass

    return entry_id


def log_tool_result(entry_id: str, tool: str, result: Any) -> None:
    """Append a JSONL result entry paired with a tool invocation."""
    traffic_path = os.environ.get(MCP_TRAFFIC_LOG_ENV, MCP_TRAFFIC_LOG_DEFAULT)
    result_entry = {
        "id": entry_id,
        "type": "result",
        "tool": tool,
        "result": result,
    }
    try:
        os.makedirs(os.path.dirname(traffic_path) or WORKSPACE_DIR, exist_ok=True)
        with open(traffic_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result_entry, default=str) + "\n")
    except Exception:
        pass
