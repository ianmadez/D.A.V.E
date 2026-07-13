import json
import os
import datetime

from tools.code_editor import rewrite_file


def safe_join(root, path):
    full_path = os.path.abspath(os.path.join(root, path))
    root_abs = os.path.abspath(root)

    if not full_path.startswith(root_abs):
        raise ValueError("Unsafe path outside project: " + str(path))

    return full_path


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_backup(target_dir, filename, content):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(target_dir, ".dave_cache", "demo_backups", timestamp)
    os.makedirs(backup_dir, exist_ok=True)

    safe_name = filename.replace("\\", "__").replace("/", "__")
    backup_path = os.path.join(backup_dir, safe_name)

    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(content)

        return backup_path


def load_recipe(recipe_path):
    if not os.path.exists(recipe_path):
        return None, "Error: Recipe file not found: " + str(recipe_path)

    try:
        with open(recipe_path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, "Error: Could not load recipe JSON: " + str(e)


def resolve_output_content(outputs, source_key):
    if not source_key:
        return None

    if source_key.startswith("outputs."):
        output_key = source_key.split(".", 1)[1]
        return outputs.get(output_key)

    return outputs.get(source_key)


def apply_demo_recipe(target_dir, recipe_filename="demo_recipe.json"):
    recipe_path = os.path.join(target_dir, recipe_filename)
    recipe, error = load_recipe(recipe_path)

    if error:
        return error

    recipe_name = recipe.get("name", "Unnamed Demo Recipe")
    outputs = recipe.get("outputs", {})
    steps = recipe.get("steps", [])

    if not steps:
        return "Error: demo_recipe.json has no steps array."

    if not outputs:
        return "Error: demo_recipe.json has no outputs object."

    report = []
    report.append("DEMO RECIPE: " + recipe_name)

    files_changed = []

    try:
        for step in steps:
            tool = step.get("tool")
            label = step.get("label", "Running step")
            filename = step.get("filename")

            report.append("- " + label)

            if tool == "read_file":
                if not filename:
                    return "Error: read_file step missing filename."

                full_path = safe_join(target_dir, filename)

                if not os.path.exists(full_path):
                    return "Error: Target file does not exist: " + filename

                content = read_text(full_path)
                line_count = len(content.splitlines())
                report.append("  Read " + filename + " (" + str(line_count) + " lines).")

            elif tool == "rewrite_file":
                if not filename:
                    return "Error: rewrite_file step missing filename."

                source_key = step.get("source")
                new_content = resolve_output_content(outputs, source_key)

                if not new_content:
                    return "Error: Missing output content for source '" + str(source_key) + "'."

                full_path = safe_join(target_dir, filename)

                if os.path.exists(full_path):
                    old_content = read_text(full_path)
                    backup_path = write_backup(target_dir, filename, old_content)
                    report.append("  Backup saved: " + backup_path)

                    result = rewrite_file(
                        filename,
                        new_content,
                        target_dir,
                        "Apply demo recipe: " + recipe_name
                        )

                if result.startswith("Error") or result.startswith("CRITICAL"):
                    return result

                    files_changed.append(filename)
                    report.append("  Updated " + filename + ".")

                else:
                    return "Error: Unknown demo recipe tool: " + str(tool)

    except Exception as e:
        return "Error applying demo recipe: " + str(e)

    report.append("")
    report.append("STATUS: SUCCESS")

    if files_changed:
        report.append("FILES_CHANGED: " + ", ".join(files_changed))
    else:
        report.append("FILES_CHANGED: None")

        run_command = recipe.get("run_command")
        open_url = recipe.get("open_url")

        if run_command:
            report.append("RUN_COMMAND: " + run_command)

            if open_url:
                report.append("OPEN_URL: " + open_url)

                return "\n".join(report)
