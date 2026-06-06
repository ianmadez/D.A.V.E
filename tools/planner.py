import os
import re
import json

# anchor_spec.json structure: {"pending":[],"in_progress":[],"completed":[]}


def _load_anchor(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"pending":[],"in_progress":[],"completed":[]}
    return {"pending":[],"in_progress":[],"completed":[]}


def _save_anchor(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2)


def manage_plan(action, data, target_dir):
    """Manage a simple todo list stored in anchor_spec.json and a markdown view."""
    plan_md = os.path.join(target_dir, "dave_plan.md")
    anchor = os.path.join(target_dir, "anchor_spec.json")

    if action == "create":
        lines = [l.strip() for l in data.split("\n") if l.strip()]
        todo = {"pending": lines.copy(), "in_progress": [], "completed": []}
        _save_anchor(anchor, todo)
        # also write markdown for human inspection
        md = "# D.A.V.E. Execution Plan\n\n"
        for i, line in enumerate(lines, 1):
            md += f"[ ] Step {i}: {line}\n"
        with open(plan_md, 'w', encoding='utf-8') as f:
            f.write(md)
        return "Anchor plan created; pending steps listed in anchor_spec.json."

    elif action == "read":
        todo = _load_anchor(anchor)
        return json.dumps(todo, indent=2)

    elif action == "complete_step":
        # data may be either an index or a description
        todo = _load_anchor(anchor)
        if not todo["pending"]:
            return "No pending steps."
        # if numeric index
        idx = None
        try:
            idx = int(data) - 1
        except Exception:
            # try to match by substring
            for i, step in enumerate(todo["pending"]):
                if data in step:
                    idx = i
                    break
        if idx is None or idx < 0 or idx >= len(todo["pending"]):
            return "Error: Step not found."
        # move step
        step = todo["pending"].pop(idx)
        todo["in_progress"].append(step)
        # mark immediately completed
        todo["in_progress"].remove(step)
        todo["completed"].append(step)
        _save_anchor(anchor, todo)
        return json.dumps(todo, indent=2)

    return "Error: Invalid action. Use 'create', 'read', or 'complete_step'."