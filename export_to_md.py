import os

OUTPUT_FILE = "project_dump.md"
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
EXCLUDE_FILES = {OUTPUT_FILE}

def write_tree(root, md):
    md.write("## 📁 Project Structure\n\n")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        level = dirpath.replace(root, "").count(os.sep)
        indent = "  " * level
        md.write(f"{indent}- {os.path.basename(dirpath)}/\n")
        for f in filenames:
            if f not in EXCLUDE_FILES:
                md.write(f"{indent}  - {f}\n")
    md.write("\n---\n\n")

def write_files(root, md):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for f in filenames:
            if f in EXCLUDE_FILES:
                continue

            filepath = os.path.join(dirpath, f)
            relpath = os.path.relpath(filepath, root)

            md.write(f"## 📄 {relpath}\n\n")

            try:
                with open(filepath, "r", encoding="utf-8") as file:
                    content = file.read()
            except:
                md.write("_Binary or unreadable file_\n\n")
                continue

            md.write("```")
            md.write("\n")
            md.write(content)
            md.write("\n```\n\n---\n\n")

def main():
    root = os.getcwd()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as md:
        write_tree(root, md)
        write_files(root, md)

if __name__ == "__main__":
    main()