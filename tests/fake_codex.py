"""Protocol fixture. Runs as a real subprocess, never calls a model service."""

import json
import sys
import time


def send(message):
    print(json.dumps(message), flush=True)


def complete():
    send(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "text": "테스트 결과입니다"}},
        }
    )
    send({"method": "turn/completed", "params": {"turn": {"id": "T1", "status": "completed"}}})


mode = ""
for line in sys.stdin:
    message = json.loads(line)
    method, identifier = message.get("method"), message.get("id")
    if method == "initialize":
        send({"id": identifier, "result": {"userAgent": "fake"}})
    elif method in {"thread/start", "thread/resume"}:
        params = message["params"]
        assert params["sandbox"] in {"read-only", "workspace-write"}
        assert params["approvalPolicy"] in {"on-request", "never"}
        if method == "thread/resume":
            assert params["threadId"] == "provider-1"
        send({"id": identifier, "result": {"thread": {"id": "provider-1"}}})
    elif method == "turn/start":
        mode = message["params"]["input"][0]["text"]
        send({"id": identifier, "result": {"turn": {"id": "T1"}}})
        if mode == "approval":
            send(
                {
                    "id": "approval-1",
                    "method": "item/commandExecution/requestApproval",
                    "params": {
                        "command": "python -m pytest",
                        "threadId": "provider-1",
                        "turnId": "T1",
                        "availableDecisions": ["accept", "decline"],
                    },
                }
            )
        elif mode == "input":
            send(
                {
                    "id": "input-1",
                    "method": "item/tool/requestUserInput",
                    "params": {"questions": [{"id": "q1", "question": "어느 환경인가요?"}]},
                }
            )
        elif mode == "hang":
            pass
        elif mode == "exit":
            sys.exit(2)
        elif mode == "malformed":
            print("not-json", flush=True)
        else:
            complete()
    elif method == "turn/interrupt":
        send({"id": identifier, "result": {}})
        break
    elif identifier == "approval-1":
        assert message["result"]["decision"] in {"accept", "decline"}
        complete()
    elif identifier == "input-1":
        assert "answers" in message["result"]
        complete()
    elif method == "slow":
        time.sleep(60)
