import subprocess
import os

def run_command(command, target_dir):
    """Batch 5.1: Executes a terminal command and normalizes output."""
    try:
        # Executes the command within the target directory
        # stdin=subprocess.DEVNULL instantly crashes interactive prompts!
        result = subprocess.run(
            command,
            shell=True,
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL 
        )
        
        output = result.stdout.strip() if result.stdout else ""
        errors = result.stderr.strip() if result.stderr else ""
        
        if result.returncode == 0:
            summary = output.split('\n')[-1] if output else "Command executed silently."
            return f"STATUS: SUCCESS\nERROR_TYPE: null\nSUMMARY: {summary[:100]}\n\n[FULL OUTPUT]\n{output}"
        else:
            summary = errors.split('\n')[-1] if errors else "Command failed with exit code."
            err_type = "Syntax" if "SyntaxError" in errors else "Logic"
            return f"STATUS: FAILURE\nERROR_TYPE: {err_type}\nSUMMARY: {summary[:100]}\n\n[FULL ERRORS]\n{errors}"
            
    except subprocess.TimeoutExpired:
        return "STATUS: FAILURE\nERROR_TYPE: Timeout\nSUMMARY: Command timed out after 15s. The script is likely blocked by an infinite loop.\n\n[SYSTEM GUIDANCE] Ensure your code does not run infinitely."
    except Exception as e:
        return f"STATUS: FAILURE\nERROR_TYPE: System\nSUMMARY: {str(e)[:100]}"