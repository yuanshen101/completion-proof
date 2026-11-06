"""Tool schemas for Completion Proof.

The schemas deliberately expose no ``argv``, ``cwd``, ``env``, ``path`` or glob
parameter. A model can ask which checks exist, run configured checks, and read
evidence; it cannot turn the plugin into arbitrary command execution.
"""

PROOF_LIST_CHECKS = {
    "name": "proof_list_checks",
    "description": (
        "List the user-configured Completion Proof checks and their declared evidence scope. "
        "Read-only. The model cannot add or modify checks; configuration lives in trusted "
        "Hermes settings."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

PROOF_STATUS = {
    "name": "proof_status",
    "description": (
        "Show the latest evidence receipt per configured check, with freshness computed against "
        "the current source/test/artifact files. A receipt becomes STALE when relevant files "
        "change. Read-only; it does not run commands."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "check_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional configured check ids. Omit to include every enabled check.",
            }
        },
        "required": [],
    },
}

PROOF_VERIFY = {
    "name": "proof_verify",
    "description": (
        "Run one or more user-configured checks (never an arbitrary command), hash the declared "
        "source/test/artifact scope, and record immutable PASS/FAIL/UNKNOWN receipts. Only "
        "configured check ids are accepted. A PASS proves only that the configured command "
        "exited 0 on the declared scope."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "check_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Configured check ids to run. Omit to run every enabled check.",
            }
        },
        "required": [],
    },
}

PROOF_REPORT = {
    "name": "proof_report",
    "description": (
        "Render a Markdown or JSON evidence report from the local ledger, including what a PASS "
        "proves and does not prove. Read-only; it does not run commands unless check ids are "
        "verified separately."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "check_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional configured check ids.",
            },
            "format": {
                "type": "string",
                "enum": ["markdown", "json"],
                "description": "Report format, default markdown.",
            },
        },
        "required": [],
    },
}
