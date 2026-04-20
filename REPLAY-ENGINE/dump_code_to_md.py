import os

# Set your root directory
ROOT_DIR = os.path.join(os.getcwd(), 'replay-engine')
OUTPUT_FILE = 'project_dump.md'
EXCLUDE_DIRS = {'.venv', '__pycache__', 'static'}  # Add more if needed
VALID_EXTENSIONS = {'.py', '.yml', '.html'}  # Add more if needed

def should_include(file_path):
    return (
        os.path.splitext(file_path)[1] in VALID_EXTENSIONS and
        not any(excluded in file_path for excluded in EXCLUDE_DIRS)
    )

with open(OUTPUT_FILE, 'w', encoding='utf-8') as out_file:
    for root, dirs, files in os.walk(ROOT_DIR):
        for file in files:
            full_path = os.path.join(root, file)
            if should_include(full_path):
                rel_path = os.path.relpath(full_path, ROOT_DIR)
                out_file.write(f"\n\n---\n### `{rel_path}`\n\n```{os.path.splitext(file)[1][1:]}\n")
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        out_file.write(f.read())
                except Exception as e:
                    out_file.write(f"# Error reading file: {e}")
                out_file.write("\n```\n")