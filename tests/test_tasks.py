from ghp.tasks import parse_tasks

MD = """# Tasks
## Phase 1
- [ ] T001 [P] [US1] Create model in src/models/x.py
- [x] T002 Wire   the   service
- [X] T010 [US12] Done upper
  - [ ] T1000 indented task
Some prose - [ ] T003 not a task
- [ ] 123 no id
- [ ] T04 too short
- [ ] T005
"""


def test_parse_markers_and_done():
    t = {x["id"]: x for x in parse_tasks(MD)}
    assert t["T001"] == {"id": "T001", "title": "Create model in src/models/x.py",
                         "done": False}
    assert t["T002"]["title"] == "Wire the service" and t["T002"]["done"] is True
    assert t["T010"]["done"] is True
    assert "T1000" in t and t["T005"]["title"] == ""


def test_parse_ignores_non_task_lines():
    assert [x["id"] for x in parse_tasks(MD)] == ["T001", "T002", "T010", "T1000", "T005"]
